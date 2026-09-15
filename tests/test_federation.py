"""
Smoke + scenario tests.  Requires the services to be running:

    python start.py --no-browser        # in one terminal
    python -m pytest -q                  # in another   (or: python tests/test_federation.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration import federation, schema_matcher  # noqa: E402

EXPECT = {
    "SLP-AMOX500-B2401-0007": "ASALI",
    "SLP-PARA650-B2405-0012": "ASALI",
    "APX-AZITH250-B2312-0003": "ASALI",
    "MRC-ORS-B2407-0071": "ASALI",
    "SLP-PANTO40-B2402-0021": "NAKALI",   # lab counterfeit
    "MRC-DOLO650-B2403-0044": "NAKALI",   # custody break
    "SLP-VITD3-B2404-0050": "NAKALI",     # over-issue
    "APX-COUGH100-B2311-0009": "NAKALI",  # expired at sale
    "SLP-INSUL-B2406-0033": "SUSPECT",    # source company mismatch
    "MRC-METFOR-B2405-0066": "SUSPECT",   # open investigation
    "ZZZ-CIPRO500-FAKE-9001": "NAKALI",   # ghost code + report
    "ZZZ-REMDES-FAKE-9099": "NAKALI",     # ghost code, unreported
}


def test_verdicts():
    for code, want in EXPECT.items():
        rec = federation.verify(code)
        got = rec["verdict"]["verdict"]
        assert got == want, f"{code}: expected {want}, got {got} (score {rec['verdict']['trust_score']})"


def test_plan_is_multi_source():
    rec = federation.verify("SLP-AMOX500-B2401-0007")
    sources = {p["source"] for p in rec["_plan"]}
    assert sources == {"manufacturer", "distributor", "vendor", "ministry"}


def test_schema_matcher_finds_global_key():
    schemas, samples = federation.gather_schema_and_samples()
    report = schema_matcher.analyze(schemas, samples)
    key = report["global_join_key"]
    assert key is not None, (
        "no global join key found - make sure all 4 services are up and the "
        "databases are freshly built (`python data/build_databases.py`); "
        "leftover test data (e.g. a report filed via the GUI) can also throw "
        "this off until you rebuild"
    )
    joined = " ".join(key["members"])
    for col in ("gtin_serial", "item_code", "productBarcode", "suspect_code"):
        assert col in joined, f"{col} missing from discovered join key"


def test_schema_matcher_rejects_licence_false_positive():
    schemas, samples = federation.gather_schema_and_samples()
    report = schema_matcher.analyze(schemas, samples)
    for cl in report["clusters"]:
        j = " ".join(cl["members"])
        assert not ("license_no" in j and "drug_license" in j), \
            "licence columns wrongly merged despite disjoint values"


def test_sql_point_lookup():
    res = federation.run_sql(
        "SELECT code, verdict, trust_score FROM item_master WHERE code = 'SLP-VITD3-B2404-0050'")
    assert res["ok"] and res["rows"][0]["verdict"] == "NAKALI"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {e}  (are the services running?)")
    sys.exit(1 if failed else 0)
