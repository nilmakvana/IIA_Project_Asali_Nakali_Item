"""
Schema matching / mapping algorithm.

The four databases were designed in isolation, so we must *discover* which
columns mean the same thing before we can federate.  We use a hybrid matcher:

    score(a, b) = 0.30 * name_similarity      (linguistic)
                + 0.10 * type_similarity      (structural)
                + 0.60 * value_similarity     (instance based  <-- dominant)

Instance-based (value) matching is what actually finds the global join key:
`gtin_serial`, `item_code`, `productBarcode`, `suspect_code` and `auth_code`
share literal code *values*, so their Jaccard overlap is high even though the
names look nothing alike.  It also *avoids false positives*: `license_no`,
`drug_license` and `shopLicense` have very similar names but disjoint values,
so the matcher correctly refuses to merge them.

Only Python's standard library is used (difflib for string similarity), so
there are no native dependencies.
"""

import difflib
import re

# name  -> weight
W_NAME, W_TYPE, W_VALUE = 0.30, 0.10, 0.60
MATCH_THRESHOLD = 0.34

_SYN = {
    "no": "number", "num": "number", "qty": "quantity", "amt": "amount",
    "desc": "description", "mfg": "manufacture", "sup": "supplier",
    "dist": "distributor", "id": "identifier", "ts": "timestamp",
    "gtin": "code", "serial": "code", "barcode": "code", "sku": "code",
    "vendor": "vendor", "auth": "authenticated", "reg": "registry",
}


def tokenize(name: str):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)          # splitCamelCase
    parts = re.split(r"[\s_\-.]+", name)
    toks = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        m = re.match(r"^([a-z]+)(\d+)$", p)                       # amox500 -> amox, 500
        toks.extend([m.group(1), m.group(2)] if m else [p])
    return [_SYN.get(t, t) for t in toks]


def name_similarity(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    jacc = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    seq = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return round(0.5 * jacc + 0.5 * seq, 4)


def infer_type(values) -> str:
    vals = [str(v).strip() for v in values if str(v).strip()][:40]
    if not vals:
        return "unknown"

    def _is_int(x):
        try:
            int(x)
            return True
        except ValueError:
            return False

    def _is_float(x):
        try:
            float(x)
            return True
        except ValueError:
            return False

    if all(re.match(r"^\d{4}-\d{2}-\d{2}", v) for v in vals):
        return "date"
    if all(_is_int(v) for v in vals):
        return "integer"
    if all(_is_float(v) for v in vals):
        return "number"
    return "text"


def type_similarity(t1: str, t2: str) -> float:
    if "unknown" in (t1, t2):
        return 0.0
    if t1 == t2:
        return 1.0
    if {t1, t2} <= {"integer", "number"}:
        return 0.6
    return 0.0


def value_similarity(a_vals, b_vals) -> float:
    A = {str(v).strip().lower() for v in a_vals if str(v).strip()}
    B = {str(v).strip().lower() for v in b_vals if str(v).strip()}
    if not A or not B:
        return 0.0
    inter = len(A & B)
    union = len(A | B)
    jaccard = inter / union
    # Containment / overlap coefficient: "what fraction of the SMALLER
    # sample's values are found in the larger sample". A small reference
    # table (e.g. a handful of counterfeit reports) matched against a large
    # transactional table (hundreds of batches) shares the same join key,
    # but plain Jaccard punishes that pairing anyway - the union is
    # dominated by the large side's unrelated values, so the ratio collapses
    # even when every value on the small side is genuinely present on the
    # large side. Containment measures exactly the thing that matters here.
    # Guarded for very small sets (<2 distinct values), where containment
    # alone would be satisfied too easily by chance (e.g. a single repeated
    # value like a country column).
    if min(len(A), len(B)) >= 2:
        containment = inter / min(len(A), len(B))
        return round(0.4 * jaccard + 0.6 * containment, 4)
    return round(jaccard, 4)


def _looks_like_surrogate_key(col) -> bool:
    """Small consecutive integers (row-id / FK) - value overlap here is an
    artefact of both tables starting their ids at 1, not a real correspondence."""
    if col["type"] != "integer":
        return False
    nums = []
    for v in col["values"]:
        try:
            nums.append(int(v))
        except (TypeError, ValueError):
            return False
    return bool(nums) and max(nums) < 1000


def _score(col_a, col_b):
    ns = name_similarity(col_a["name"], col_b["name"])
    ts = type_similarity(col_a["type"], col_b["type"])
    vs = value_similarity(col_a["values"], col_b["values"])
    # instance evidence from two surrogate-key columns is spurious unless the
    # names also agree - discount it so ids don't all collapse into one cluster.
    if _looks_like_surrogate_key(col_a) and _looks_like_surrogate_key(col_b) and ns < 0.55:
        vs = round(vs * 0.1, 4)
    total = round(W_NAME * ns + W_TYPE * ts + W_VALUE * vs, 4)
    return total, {"name": ns, "type": ts, "value": vs}


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


SHORT = {"manufacturer": "mfg", "distributor": "dist", "vendor": "ven", "ministry": "min"}

# columns highlighted in the GUI similarity matrix (key cluster + a "trap")
FOCUS = [
    ("manufacturer", "manufactured_batches", "gtin_serial"),
    ("distributor", "distributed_stock", "item_code"),
    ("vendor", "purchaseScans", "productBarcode"),
    ("ministry", "counterfeit_reports", "suspect_code"),
    ("ministry", "verified_genuine_registry", "auth_code"),
    ("manufacturer", "companies", "company_name"),
    ("distributor", "inbound_consignments", "source_company"),
    ("distributor", "suppliers", "sup_name"),
    ("ministry", "counterfeit_reports", "reported_against_supplier"),
    ("manufacturer", "companies", "license_no"),
    ("distributor", "suppliers", "drug_license"),
]


def analyze(schemas: dict, samples: dict) -> dict:
    """
    schemas : {source: {table: [{name, type}, ...]}}
    samples : {source: {table: {column: [values]}}}
    """
    # flatten every column into one comparable list
    cols = []
    for source, tables in schemas.items():
        for table, collist in tables.items():
            for col in collist:
                vals = samples.get(source, {}).get(table, {}).get(col["name"], [])
                cols.append(
                    {
                        "key": f"{source}.{table}.{col['name']}",
                        "label": f"{SHORT[source]}.{col['name']}",
                        "source": source,
                        "table": table,
                        "name": col["name"],
                        "declared_type": col["type"],
                        "type": infer_type(vals) if vals else _sqlite_type(col["type"]),
                        "values": vals,
                    }
                )

    # all-pairs comparison (skip same-source pairs: isolation assumption)
    pairs = []
    uf = _UnionFind()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            if cols[i]["source"] == cols[j]["source"]:
                continue
            total, parts = _score(cols[i], cols[j])
            if total >= MATCH_THRESHOLD:
                pairs.append(
                    {
                        "a": cols[i]["key"], "b": cols[j]["key"],
                        "score": total, "components": parts,
                    }
                )
                uf.union(cols[i]["key"], cols[j]["key"])

    # build clusters
    groups = {}
    for c in cols:
        if c["key"] in uf.parent:
            groups.setdefault(uf.find(c["key"]), []).append(c["key"])
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_pairs = [p for p in pairs if p["a"] in members and p["b"] in members]
        min_score = min((p["score"] for p in member_pairs), default=0.0)
        clusters.append(
            {
                "members": sorted(members),
                "size": len(members),
                "min_pair_score": round(min_score, 4),
                "pairs": member_pairs,
            }
        )
    clusters.sort(key=lambda c: (-c["size"], -c["min_pair_score"]))

    # the global join key = biggest cluster that contains the code columns
    code_hint = "manufactured_batches.gtin_serial"
    join_key = next(
        (c for c in clusters if any(code_hint in m for m in c["members"])), None
    )

    # focused matrix for the GUI heat-map
    focus_cols = []
    for src, tbl, name in FOCUS:
        match = next((c for c in cols if c["key"] == f"{src}.{tbl}.{name}"), None)
        if match:
            focus_cols.append(match)
    matrix = []
    for a in focus_cols:
        cells = []
        for b in focus_cols:
            if a["key"] == b["key"]:
                cells.append({"score": 1.0, "self": True})
            elif a["source"] == b["source"]:
                cells.append({"score": None, "same_source": True})
            else:
                total, _ = _score(a, b)
                cells.append({"score": total, "match": total >= MATCH_THRESHOLD})
        matrix.append({"label": a["label"], "key": a["key"], "cells": cells})

    return {
        "weights": {"name": W_NAME, "type": W_TYPE, "value": W_VALUE},
        "threshold": MATCH_THRESHOLD,
        "column_count": len(cols),
        "cross_source_pairs_matched": len(pairs),
        "clusters": clusters,
        "global_join_key": join_key,
        "matrix": {"labels": [c["label"] for c in focus_cols], "rows": matrix},
        "all_pairs": sorted(pairs, key=lambda p: -p["score"]),
    }


def _sqlite_type(declared: str) -> str:
    d = (declared or "").upper()
    if "INT" in d:
        return "integer"
    if any(k in d for k in ("REAL", "FLOA", "DOUB", "NUM")):
        return "number"
    return "text"
