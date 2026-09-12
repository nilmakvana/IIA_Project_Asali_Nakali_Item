"""
The mediator / federation engine.

Nothing here touches a database file directly - every fact is fetched over HTTP
from the four independent data-source services.  Responsibilities:

  1. decompose a request against the mediated schema into per-source sub-queries
  2. dispatch the sub-queries in parallel (concurrent.futures)
  3. integrate the answers on the discovered join key (the item code)
  4. run the rule-based authenticity engine -> ASALI / SUSPECT / NAKALI
  5. build the provenance timeline + list schema-level value conflicts
  6. expose a tiny SQL front-end for the two mediated views

Every response carries a `_plan` array so the GUI can show the decomposition
(which SQL ran on which source and how long it took).
"""

import concurrent.futures as _cf
import datetime as _dt
import re
import time

import requests

import config

_TIMEOUT = 8
_TODAY = _dt.date.today().isoformat()


# --------------------------------------------------------------------------- #
#  low level: one sub-query against one source                               #
# --------------------------------------------------------------------------- #
def _subquery(source, table, where=None, columns=None, limit=2000):
    url = f"{config.service_url(source)}/query"
    body = {"table": table, "where": where or {}, "columns": columns, "limit": limit}
    t0 = time.perf_counter()
    try:
        r = requests.post(url, json=body, timeout=_TIMEOUT)
        j = r.json()
        j["ok"] = r.ok
    except Exception as exc:  # network / service down
        j = {"ok": False, "source": source, "table": table, "error": str(exc), "rows": [], "sql": None}
    j.setdefault("elapsed_ms", round((time.perf_counter() - t0) * 1000, 2))
    j.setdefault("rows", [])
    return j


def _dispatch(tasks: dict) -> dict:
    """tasks = {label: (source, table, where, columns)}  -> {label: response}"""
    out = {}
    with _cf.ThreadPoolExecutor(max_workers=max(1, len(tasks))) as ex:
        futs = {ex.submit(_subquery, *args): label for label, args in tasks.items()}
        for fut in _cf.as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


def _plan_rows(responses, step_start=1):
    plan = []
    for i, (label, j) in enumerate(responses.items(), start=step_start):
        plan.append(
            {
                "step": i,
                "label": label,
                "source": j.get("source"),
                "table": j.get("table"),
                "sql": j.get("sql"),
                "params": j.get("params"),
                "rowcount": j.get("rowcount", len(j.get("rows", []))),
                "elapsed_ms": j.get("elapsed_ms"),
                "ok": j.get("ok", True),
                "error": j.get("error"),
            }
        )
    return plan


# --------------------------------------------------------------------------- #
#  the headline operation: full cross-source history + verdict for one code  #
# --------------------------------------------------------------------------- #
def verify(code: str) -> dict:
    code = (code or "").strip()
    t_start = time.perf_counter()
    plan = []

    # ---- round 1 : hit every source on the code (fully parallel) -----------
    r1 = _dispatch(
        {
            "manufacturer.manufactured_batches": (
                "manufacturer", "manufactured_batches",
                {"gtin_serial": {"op": "=", "value": code}}, None),
            "distributor.distributed_stock": (
                "distributor", "distributed_stock",
                {"item_code": {"op": "=", "value": code}}, None),
            "vendor.purchaseScans": (
                "vendor", "purchaseScans",
                {"productBarcode": {"op": "=", "value": code}}, None),
            "ministry.counterfeit_reports": (
                "ministry", "counterfeit_reports",
                {"suspect_code": {"op": "=", "value": code}}, None),
            "ministry.verified_genuine_registry": (
                "ministry", "verified_genuine_registry",
                {"auth_code": {"op": "=", "value": code}}, None),
        }
    )
    plan += _plan_rows(r1, 1)

    batches = r1["manufacturer.manufactured_batches"]["rows"]
    dist = r1["distributor.distributed_stock"]["rows"]
    scans = r1["vendor.purchaseScans"]["rows"]
    reports = r1["ministry.counterfeit_reports"]["rows"]
    registry = r1["ministry.verified_genuine_registry"]["rows"]

    # ---- round 2 : dependent look-ups -------------------------------------
    tasks2 = {}
    prod_ids = sorted({b["product_id"] for b in batches})
    cons_ids = sorted({d["consignment_id"] for d in dist})
    scan_ids = sorted({s["scanId"] for s in scans})
    report_refs = sorted({r["report_ref"] for r in reports})
    vendor_ids = sorted({s["vendorId"] for s in scans})

    if prod_ids:
        tasks2["manufacturer.products"] = (
            "manufacturer", "products", {"product_id": {"op": "IN", "value": prod_ids}}, None)
    if cons_ids:
        tasks2["distributor.inbound_consignments"] = (
            "distributor", "inbound_consignments",
            {"consignment_id": {"op": "IN", "value": cons_ids}}, None)
    if scan_ids:
        tasks2["vendor.customerSales"] = (
            "vendor", "customerSales", {"scanId": {"op": "IN", "value": scan_ids}}, None)
    if vendor_ids:
        tasks2["vendor.vendors"] = (
            "vendor", "vendors", {"vendorId": {"op": "IN", "value": vendor_ids}}, None)
    if report_refs:
        tasks2["ministry.enforcement_actions"] = (
            "ministry", "enforcement_actions",
            {"report_ref": {"op": "IN", "value": report_refs}}, None)

    r2 = _dispatch(tasks2) if tasks2 else {}
    plan += _plan_rows(r2, len(plan) + 1)

    products = r2.get("manufacturer.products", {}).get("rows", [])
    consignments = r2.get("distributor.inbound_consignments", {}).get("rows", [])
    sales = r2.get("vendor.customerSales", {}).get("rows", [])
    vendors = r2.get("vendor.vendors", {}).get("rows", [])
    enforcement = r2.get("ministry.enforcement_actions", {}).get("rows", [])

    # ---- round 3 : company + supplier names ------------------------------
    tasks3 = {}
    company_ids = sorted({p["company_id"] for p in products})
    sup_ids = sorted({c["sup_id"] for c in consignments})
    if company_ids:
        tasks3["manufacturer.companies"] = (
            "manufacturer", "companies", {"company_id": {"op": "IN", "value": company_ids}}, None)
    if sup_ids:
        tasks3["distributor.suppliers"] = (
            "distributor", "suppliers", {"sup_id": {"op": "IN", "value": sup_ids}}, None)
    r3 = _dispatch(tasks3) if tasks3 else {}
    plan += _plan_rows(r3, len(plan) + 1)

    companies = r3.get("manufacturer.companies", {}).get("rows", [])
    suppliers = r3.get("distributor.suppliers", {}).get("rows", [])

    # ------------------------------------------------------------------ #
    #  INTEGRATE                                                        #
    # ------------------------------------------------------------------ #
    raw = {
        "manufacturer": {"manufactured_batches": batches, "products": products, "companies": companies},
        "distributor": {"distributed_stock": dist, "inbound_consignments": consignments, "suppliers": suppliers},
        "vendor": {"purchaseScans": scans, "customerSales": sales, "vendors": vendors},
        "ministry": {"counterfeit_reports": reports, "verified_genuine_registry": registry,
                     "enforcement_actions": enforcement},
    }

    batch = batches[0] if batches else None
    product = products[0] if products else None
    company = companies[0] if companies else None
    prod_by_id = {p["product_id"]: p for p in products}
    comp_by_id = {c["company_id"]: c for c in companies}
    cons_by_id = {c["consignment_id"]: c for c in consignments}
    sup_by_id = {s["sup_id"]: s for s in suppliers}

    source_companies = sorted({cons_by_id.get(d["consignment_id"], {}).get("source_company")
                               for d in dist if cons_by_id.get(d["consignment_id"])})
    supplier_names = sorted({sup_by_id.get(cons_by_id.get(d["consignment_id"], {}).get("sup_id"), {}).get("sup_name")
                             for d in dist if cons_by_id.get(d["consignment_id"])})
    supplied_vendors = sorted({d["supplied_to_vendor"] for d in dist})
    scan_vendor_names = sorted({v["vendorName"] for v in vendors})
    distributed_qty = sum(d["qty_supplied"] for d in dist)
    selling_prices = sorted({s["sellingPrice"] for s in scans if s.get("sellingPrice") is not None})

    master = {
        "code": code,
        "product_name": (product or {}).get("brand_name"),
        "generic_name": (product or {}).get("generic_name"),
        "dosage_form": (product or {}).get("dosage_form"),
        "strength": (product or {}).get("strength"),
        "company": (company or {}).get("company_name"),
        "manufacturer_license": (company or {}).get("license_no"),
        "factory_location": (batch or {}).get("factory_location"),
        "mfg_date": (batch or {}).get("mfg_date"),
        "expiry_date": (batch or {}).get("expiry_date"),
        "batch_qty": (batch or {}).get("batch_qty"),
        "mrp": (batch or {}).get("mrp"),
        "distributed_qty": distributed_qty,
        "distribution_events": len(dist),
        "distribution_source_companies": source_companies,
        "suppliers": supplier_names,
        "supplied_to_vendors": supplied_vendors,
        "retail_vendors": scan_vendor_names,
        "times_scanned": len(scans),
        "times_sold": len(sales),
        "selling_prices": selling_prices,
        "in_verified_registry": bool(registry),
        "verifying_authority": (registry[0]["verifying_authority"] if registry else None),
        "counterfeit_reports": [
            {k: r[k] for k in ("report_ref", "complaint_date", "lab_result", "status", "remarks",
                               "reported_by_vendor", "reported_against_supplier")}
            for r in reports
        ],
        "enforcement_actions": enforcement,
        "has_counterfeit_report": bool(reports),
        "lab_result": (reports[0]["lab_result"] if reports else None),
    }

    verdict = _verdict(master, dist, scans, sales, reports, registry, batch, company, cons_by_id)
    timeline = _timeline(code, batch, company, dist, cons_by_id, sup_by_id, scans, sales,
                         vendors, registry, reports, enforcement)
    conflicts = _conflicts(batch, product, dist, scans, company, source_companies)

    for row in plan:
        row.setdefault("elapsed_ms", 0)

    return {
        "code": code,
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "sources_contacted": len(config.SERVICES),
        "master": master,
        "verdict": verdict,
        "timeline": timeline,
        "conflicts": conflicts,
        "raw": raw,
        "_plan": plan,
        "_total_ms": round((time.perf_counter() - t_start) * 1000, 2),
    }


# --------------------------------------------------------------------------- #
#  rule-based authenticity engine                                            #
# --------------------------------------------------------------------------- #
def _verdict(master, dist, scans, sales, reports, registry, batch, company, cons_by_id):
    score = 100
    flags = []

    def flag(sev, code_, title, detail, delta):
        nonlocal score
        score += delta
        flags.append({"severity": sev, "code": code_, "title": title,
                      "detail": detail, "score_impact": delta})

    hard_nakali = False

    # 1. ghost code -------------------------------------------------------
    if batch is None:
        hard_nakali = True
        flag("CRITICAL", "GHOST_CODE",
             "No genuine batch on record",
             "This code was never registered by any manufacturer - it did not "
             "come out of a real factory.", -70)

    # 2. lab-confirmed counterfeit -------------------------------------------
    lab_bad = [r for r in reports if (r.get("lab_result") or "").upper() == "COUNTERFEIT"]
    if lab_bad:
        hard_nakali = True
        flag("CRITICAL", "LAB_COUNTERFEIT",
             "Ministry lab result: COUNTERFEIT",
             f"Report {lab_bad[0]['report_ref']} dated {lab_bad[0]['complaint_date']}: "
             f"{lab_bad[0].get('remarks', '')}", -80)

    # 3. open investigation -----------------------------------------------
    open_reps = [r for r in reports if (r.get("lab_result") or "").upper() not in ("COUNTERFEIT", "GENUINE")]
    if open_reps:
        flag("WARNING", "OPEN_INVESTIGATION",
             "Under investigation by the Ministry",
             f"Report {open_reps[0]['report_ref']} is {open_reps[0].get('status')} "
             f"(lab result {open_reps[0].get('lab_result')}).", -35)

    # 4. distribution source vs manufacturer of record ---------------------
    if batch is not None and company is not None and dist:
        src = set(master["distribution_source_companies"])
        if src and company["company_name"] not in src:
            flag("CRITICAL", "SOURCE_COMPANY_MISMATCH",
                 "Distribution source disputes the manufacturer",
                 f"Manufacturer of record is '{company['company_name']}', but the "
                 f"distributor logged the consignment source as {sorted(src)}.", -50)

    # 5. broken chain of custody -----------------------------------------
    if dist and scans:
        supplied = set(master["supplied_to_vendors"])
        sold_at = set(master["retail_vendors"])
        stray = sold_at - supplied
        if stray and supplied and sold_at.isdisjoint(supplied):
            flag("CRITICAL", "CUSTODY_BREAK",
                 "Chain of custody broken",
                 f"Distributor supplied this code only to {sorted(supplied)}, but it "
                 f"surfaced for sale at {sorted(sold_at)} - none of the legitimate "
                 f"recipients.", -65)
        elif stray:
            flag("WARNING", "CUSTODY_PARTIAL",
                 "Sold at an un-supplied outlet",
                 f"{sorted(stray)} sold this code although the distributor never "
                 f"supplied them.", -35)

    # 6. quantity over-issue (code cloning) ------------------------------
    if batch is not None:
        made = batch["batch_qty"] or 0
        moved = max(master["distributed_qty"], master["times_sold"])
        if made and moved > made:
            ratio = moved / made
            sev, delta = ("CRITICAL", -70) if ratio >= 2 else ("WARNING", -45)
            flag(sev, "QUANTITY_OVER_ISSUE",
                 "More units in the market than were manufactured",
                 f"Batch was made in a quantity of {made}, but {moved} units have "
                 f"been distributed/sold ({ratio:.1f}x) - the code has been cloned.",
                 delta)

    # 7. expired at point of sale --------------------------------------
    if batch is not None:
        exp = batch["expiry_date"]
        late = [s for s in sales if s["saleTime"][:10] > exp]
        if late:
            flag("CRITICAL", "EXPIRED_AT_SALE",
                 "Sold after the expiry date",
                 f"Expiry is {exp}; {len(late)} sale(s) recorded afterwards "
                 f"(latest {max(s['saleTime'] for s in late)}).", -65)
        elif exp < _TODAY and scans:
            flag("INFO", "EXPIRED_ON_SHELF",
                 "Expired stock still on the shelf",
                 f"Batch expired on {exp}.", -10)

    # 8. not in the verified registry (minor) --------------------------
    if batch is not None and not registry:
        flag("INFO", "NOT_IN_VERIFIED_REGISTRY",
             "Not listed in the verified-genuine registry",
             "The batch looks genuine but was never added to the Ministry's "
             "verified registry (registration is voluntary).", -5)

    # 9. retail price anomaly ---------------------------------------
    if batch is not None and batch.get("mrp"):
        mrp = batch["mrp"]
        for p in master["selling_prices"]:
            if p > 1.4 * mrp or p < 0.4 * mrp:
                flag("WARNING", "PRICE_ANOMALY",
                     "Retail price far from MRP",
                     f"MRP is {mrp}; observed retail price {p}.", -10)
                break

    score = max(0, min(100, score))
    if hard_nakali or score < 40:
        label = "NAKALI"
    elif score < 75:
        label = "SUSPECT"
    else:
        label = "ASALI"

    headline = {
        "NAKALI": "Counterfeit / spurious - do NOT buy or dispense",
        "SUSPECT": "Provenance inconsistent - verify before use",
        "ASALI": "Genuine - full chain of custody confirmed",
    }[label]

    flags.sort(key=lambda f: {"CRITICAL": 0, "WARNING": 1, "INFO": 2}[f["severity"]])
    return {
        "verdict": label,
        "trust_score": score,
        "headline": headline,
        "is_genuine": label == "ASALI",
        "red_flags": flags,
        "rule_count_triggered": len(flags),
    }


# --------------------------------------------------------------------------- #
#  provenance timeline                                                       #
# --------------------------------------------------------------------------- #
def _timeline(code, batch, company, dist, cons_by_id, sup_by_id, scans, sales,
              vendors, registry, reports, enforcement):
    ev = []
    vname = {v["vendorId"]: v["vendorName"] for v in vendors}

    if batch:
        ev.append({
            "date": batch["mfg_date"], "stage": "MANUFACTURED", "source": "manufacturer",
            "title": f"Batch manufactured by {(company or {}).get('company_name', '?')}",
            "detail": {"factory": batch["factory_location"], "batch_qty": batch["batch_qty"],
                       "mrp": batch["mrp"], "expiry_date": batch["expiry_date"]},
        })
    for r in registry:
        ev.append({
            "date": r["verified_on"], "stage": "REGISTERED", "source": "ministry",
            "title": f"Listed in verified-genuine registry ({r['verifying_authority']})",
            "detail": {"auth_code": r["auth_code"]},
        })
    for d in dist:
        c = cons_by_id.get(d["consignment_id"], {})
        s = sup_by_id.get(c.get("sup_id"), {})
        ev.append({
            "date": d["dispatch_date"], "stage": "DISTRIBUTED", "source": "distributor",
            "title": f"{s.get('sup_name', 'Distributor')} shipped {d['qty_supplied']} "
                     f"to {d['supplied_to_vendor']}",
            "detail": {"source_company": c.get("source_company"), "unit_price": d["unit_price"],
                       "invoice": c.get("invoice_no"), "product_desc": d["product_desc"]},
        })
    for sc in scans:
        ev.append({
            "date": sc["scanTimestamp"][:10], "stage": "SCANNED_IN", "source": "vendor",
            "title": f"Scanned into stock at {vname.get(sc['vendorId'], '?')}",
            "detail": {"label": sc["scannedLabelName"], "pack": sc["packSize"],
                       "selling_price": sc["sellingPrice"], "note": sc.get("cashierNote")},
        })
    scan_vendor = {sc["scanId"]: vname.get(sc["vendorId"], "?") for sc in scans}
    for sa in sales:
        ev.append({
            "date": sa["saleTime"][:10], "stage": "SOLD", "source": "vendor",
            "title": f"Sold to a customer at {scan_vendor.get(sa['scanId'], '?')}",
            "detail": {"bill_no": sa["billNo"]},
        })
    for r in reports:
        ev.append({
            "date": r["complaint_date"], "stage": "REPORTED", "source": "ministry",
            "title": f"Counterfeit report {r['report_ref']} ({r['status']}, lab {r['lab_result']})",
            "detail": {"by": r["reported_by_vendor"], "against": r["reported_against_supplier"],
                       "remarks": r["remarks"]},
        })
    for a in enforcement:
        ev.append({
            "date": a["action_date"], "stage": "ENFORCEMENT", "source": "ministry",
            "title": a["action_taken"],
            "detail": {"penalty_amount": a["penalty_amount"], "report_ref": a["report_ref"]},
        })
    ev.sort(key=lambda e: (e["date"] or "9999", e["stage"]))
    return ev


# --------------------------------------------------------------------------- #
#  schema-level value conflicts across the isolated sources                  #
# --------------------------------------------------------------------------- #
def _conflicts(batch, product, dist, scans, company, source_companies):
    out = []

    names = {}
    if product:
        names["manufacturer.products.brand_name"] = product["brand_name"]
    for d in dist[:1]:
        names["distributor.distributed_stock.product_desc"] = d["product_desc"]
    for s in scans[:1]:
        names["vendor.purchaseScans.scannedLabelName"] = s["scannedLabelName"]
    if len({v.lower().replace(" ", "") for v in names.values()}) > 1:
        out.append({
            "attribute": "product_name", "kind": "REPRESENTATIONAL", "benign": True,
            "by_source": names,
            "resolution": "kept all; brand name from manufacturer used as canonical",
        })

    if company and source_companies:
        vals = {"manufacturer.companies.company_name": company["company_name"]}
        for i, sc in enumerate(source_companies):
            vals[f"distributor.inbound_consignments.source_company[{i}]"] = sc
        if company["company_name"] not in source_companies:
            out.append({
                "attribute": "company", "kind": "SEMANTIC_MISMATCH", "benign": False,
                "by_source": vals,
                "resolution": "flagged as SOURCE_COMPANY_MISMATCH - provenance broken",
            })

    prices = {}
    if batch and batch.get("mrp") is not None:
        prices["manufacturer.manufactured_batches.mrp"] = batch["mrp"]
    for d in dist[:1]:
        prices["distributor.distributed_stock.unit_price"] = d["unit_price"]
    for s in scans[:1]:
        prices["vendor.purchaseScans.sellingPrice"] = s["sellingPrice"]
    if len(set(prices.values())) > 1:
        out.append({
            "attribute": "price", "kind": "REPRESENTATIONAL", "benign": True,
            "by_source": prices,
            "resolution": "expected: MRP vs wholesale vs retail; no merge",
        })
    return out


# --------------------------------------------------------------------------- #
#  tiny SQL front-end for the two mediated views                             #
# --------------------------------------------------------------------------- #
_SQL_RE = re.compile(
    r"^\s*select\s+(?P<cols>.+?)\s+from\s+(?P<view>item_master|item_authenticity)"
    r"(?:\s+where\s+code\s*=\s*'(?P<code>[^']+)')?"
    r"(?:\s+limit\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)

_MASTER_COLS = [
    "code", "product_name", "generic_name", "company", "manufacturer_license",
    "factory_location", "mfg_date", "expiry_date", "batch_qty", "mrp",
    "distributed_qty", "times_scanned", "times_sold", "in_verified_registry",
    "has_counterfeit_report", "lab_result", "verdict", "trust_score",
]
_AUTH_COLS = ["code", "verdict", "trust_score", "red_flags", "headline"]


def run_sql(sql: str) -> dict:
    m = _SQL_RE.match(sql or "")
    if not m:
        return {
            "ok": False,
            "error": "Only  SELECT <cols> FROM item_master|item_authenticity "
                     "[WHERE code = '...'] [LIMIT n]  is supported by this mediator.",
        }
    view = m.group("view").lower()
    code = m.group("code")
    limit = int(m.group("limit")) if m.group("limit") else 50
    want = m.group("cols").strip()
    allcols = _MASTER_COLS if view == "item_master" else _AUTH_COLS
    cols = allcols if want == "*" else [c.strip() for c in want.split(",")]
    bad = [c for c in cols if c not in allcols]
    if bad:
        return {"ok": False, "error": f"unknown column(s) for {view}: {bad}",
                "available": allcols}

    if code:
        return _sql_point_lookup(view, code, cols)
    return _sql_scan(view, cols, limit)


def _project(rec, cols):
    m = rec["master"]
    v = rec["verdict"]
    full = {
        **{k: m.get(k) for k in _MASTER_COLS if k in m},
        "verdict": v["verdict"],
        "trust_score": v["trust_score"],
        "headline": v["headline"],
        "red_flags": [f["code"] for f in v["red_flags"]],
        "code": rec["code"],
    }
    return {c: full.get(c) for c in cols}


def _sql_point_lookup(view, code, cols):
    rec = verify(code)
    return {
        "ok": True,
        "mode": "point lookup - 4-source federation on the discovered join key",
        "view": view,
        "sql_in": f"SELECT {', '.join(cols)} FROM {view} WHERE code = '{code}'",
        "decomposition": rec["_plan"],
        "rows": [_project(rec, cols)],
        "rowcount": 1,
        "elapsed_ms": rec["_total_ms"],
    }


def _sql_scan(view, cols, limit):
    """Multi-row query -> decomposed into a 2-source federated join
    (manufacturer x ministry) executed over the REST APIs."""
    plan_resp = _dispatch(
        {
            "manufacturer.manufactured_batches": (
                "manufacturer", "manufactured_batches", None,
                ["batch_id", "product_id", "gtin_serial", "mfg_date", "expiry_date",
                 "batch_qty", "mrp", "factory_location"]),
            "manufacturer.products": ("manufacturer", "products", None, None),
            "manufacturer.companies": ("manufacturer", "companies", None, None),
            "ministry.counterfeit_reports": ("ministry", "counterfeit_reports", None, None),
            "ministry.verified_genuine_registry": ("ministry", "verified_genuine_registry", None, None),
        }
    )
    plan = _plan_rows(plan_resp, 1)

    batches = plan_resp["manufacturer.manufactured_batches"]["rows"]
    prod = {p["product_id"]: p for p in plan_resp["manufacturer.products"]["rows"]}
    comp = {c["company_id"]: c for c in plan_resp["manufacturer.companies"]["rows"]}
    reps = plan_resp["ministry.counterfeit_reports"]["rows"]
    reg = {r["auth_code"] for r in plan_resp["ministry.verified_genuine_registry"]["rows"]}
    rep_by_code = {}
    for r in reps:
        rep_by_code.setdefault(r["suspect_code"], []).append(r)

    rows = []
    for b in batches[:limit]:
        p = prod.get(b["product_id"], {})
        c = comp.get(p.get("company_id"), {})
        code = b["gtin_serial"]
        r = rep_by_code.get(code, [])
        lab = r[0]["lab_result"] if r else None
        if lab == "COUNTERFEIT":
            verdict, ts = "NAKALI", 15
        elif r:
            verdict, ts = "SUSPECT", 55
        elif code in reg:
            verdict, ts = "ASALI", 95
        else:
            verdict, ts = "ASALI", 90
        full = {
            "code": code, "product_name": p.get("brand_name"),
            "generic_name": p.get("generic_name"), "company": c.get("company_name"),
            "manufacturer_license": c.get("license_no"),
            "factory_location": b.get("factory_location"),
            "mfg_date": b.get("mfg_date"), "expiry_date": b.get("expiry_date"),
            "batch_qty": b.get("batch_qty"), "mrp": b.get("mrp"),
            "distributed_qty": None, "times_scanned": None, "times_sold": None,
            "in_verified_registry": code in reg,
            "has_counterfeit_report": bool(r), "lab_result": lab,
            "verdict": verdict, "trust_score": ts,
            "red_flags": [], "headline": None,
        }
        rows.append({k: full.get(k) for k in cols})

    return {
        "ok": True,
        "mode": "multi-row scan - decomposed into a 2-source federated join "
                "(manufacturer ⋈ ministry) over REST",
        "view": "item_master",
        "sql_in": f"SELECT {', '.join(cols)} FROM item_master LIMIT {limit}",
        "decomposition": plan,
        "note": "this multi-row path uses a lightweight 2-source verdict "
                "(manufacturer + ministry only). Ghost codes (no manufacturer "
                "batch) and chain-of-custody / quantity checks are not visible "
                "here - use  WHERE code = '...'  for the full 4-source verdict.",
        "rows": rows,
        "rowcount": len(rows),
    }


# --------------------------------------------------------------------------- #
#  file a counterfeit report back to the Ministry (write-back / closed loop) #
# --------------------------------------------------------------------------- #
def file_report(code: str, rec: dict = None) -> dict:
    rec = rec or verify(code)
    m = rec["master"]
    v = rec["verdict"]
    against = (m["suppliers"] or [None])[0]
    body = {
        "suspect_code": code,
        "reported_by_vendor": (m["retail_vendors"] or [""])[0],
        "reported_against_supplier": against or "UNKNOWN",
        "lab_result": "COUNTERFEIT" if v["verdict"] == "NAKALI" else "PENDING",
        "status": "OPEN",
        "remarks": "Auto-filed by the authenticity mediator. Red flags: "
                   + ", ".join(f["code"] for f in v["red_flags"]),
        "filed_by": "authenticity-mediator",
    }
    try:
        r = requests.post(f"{config.service_url('ministry')}/reports", json=body, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return {
            "ok": False,
            "request": body,
            "error": f"could not reach the ministry service at "
                     f"{config.service_url('ministry')}: {exc}",
        }
    try:
        response_json = r.json()
    except ValueError:
        response_json = {"error": f"ministry service returned non-JSON (HTTP {r.status_code})"}
    return {"ok": r.ok, "request": body, "response": response_json, "http_status": r.status_code}


def source_health() -> list:
    out = []
    for name in config.SERVICES:
        t0 = time.perf_counter()
        try:
            r = requests.get(f"{config.service_url(name)}/health", timeout=4)
            j = r.json()
            out.append({
                "source": name, "url": config.service_url(name), "status": "up",
                "row_counts": j.get("row_counts", {}),
                "total_rows": sum(j.get("row_counts", {}).values()),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            })
        except Exception as exc:
            out.append({"source": name, "url": config.service_url(name),
                        "status": "down", "error": str(exc)})
    return out


def gather_schema_and_samples():
    schemas, samples = {}, {}
    for name in config.SERVICES:
        schemas[name] = requests.get(f"{config.service_url(name)}/schema", timeout=5).json()["schema"]
        samples[name] = requests.get(f"{config.service_url(name)}/sample", timeout=5).json()["samples"]
    return schemas, samples
