"""
Ministry of Consumer Affairs data source.

Read API is the generic one.  Two extra endpoints support the closed loop:

    GET  /reports?code=<code>     -> counterfeit reports for a code (+enforcement)
    POST /reports  {suspect_code, reported_by_vendor, reported_against_supplier,
                    lab_result, status, remarks, filed_by}
        -> inserts a new counterfeit report, auto-generates MOCA-YYYY-NNNN ref,
           and (if lab_result == COUNTERFEIT) calls the distributor service to
           blacklist the named supplier.
"""
import datetime as _dt
import os
import sqlite3
import sys
from typing import Optional

import requests
from fastapi import Body
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from datasources.base import make_service, run_standalone  # noqa: E402

DB = config.SERVICES["ministry"]["db"]


def _next_ref(con):
    year = _dt.date.today().year
    row = con.execute(
        "SELECT report_ref FROM counterfeit_reports "
        "WHERE report_ref LIKE ? ORDER BY report_ref DESC LIMIT 1",
        (f"MOCA-{year}-%",),
    ).fetchone()
    seq = int(row[0].split("-")[-1]) + 1 if row else 1
    return f"MOCA-{year}-{seq:04d}"


def create_app():
    app = make_service("ministry", DB)

    @app.get("/reports")
    def get_reports(code: Optional[str] = None):
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        try:
            if code:
                reps = [dict(r) for r in con.execute(
                    "SELECT * FROM counterfeit_reports WHERE suspect_code = ?", (code,))]
            else:
                reps = [dict(r) for r in con.execute("SELECT * FROM counterfeit_reports")]
            refs = [r["report_ref"] for r in reps]
            actions = []
            if refs:
                q = ",".join("?" * len(refs))
                actions = [dict(r) for r in con.execute(
                    f"SELECT * FROM enforcement_actions WHERE report_ref IN ({q})", refs)]
        finally:
            con.close()
        return {"source": "ministry", "reports": reps, "enforcement_actions": actions}

    @app.post("/reports")
    def post_report(b: dict = Body(default={})):
        code = (b.get("suspect_code") or "").strip()
        if not code:
            return JSONResponse({"error": "suspect_code is required"}, status_code=400)

        con = sqlite3.connect(DB)
        try:
            ref = _next_ref(con)
            row = (
                ref,
                code,
                b.get("reported_by_vendor", ""),
                b.get("reported_against_supplier", ""),
                _dt.date.today().isoformat(),
                b.get("lab_result", "PENDING"),
                b.get("status", "OPEN"),
                b.get("remarks", "Auto-filed by the authenticity mediator."),
                b.get("filed_by", "authenticity-mediator"),
            )
            con.execute(
                "INSERT INTO counterfeit_reports VALUES (?,?,?,?,?,?,?,?,?)", row
            )
            con.commit()
        finally:
            con.close()

        # ---- closed loop: notify the distributor source ----------------------
        propagation = None
        supplier = b.get("reported_against_supplier")
        if b.get("lab_result") == "COUNTERFEIT" and supplier:
            try:
                r = requests.post(
                    f"{config.service_url('distributor')}/flag-supplier",
                    json={"supplier_name": supplier, "report_ref": ref,
                          "reason": "Named in a confirmed counterfeit report"},
                    timeout=5,
                )
                propagation = r.json()
            except Exception as exc:  # pragma: no cover - best effort
                propagation = {"error": str(exc)}

        return JSONResponse(
            {
                "source": "ministry",
                "created": {
                    "report_ref": ref,
                    "suspect_code": code,
                    "complaint_date": row[4],
                    "lab_result": row[5],
                    "status": row[6],
                },
                "supplier_flag_propagation": propagation,
            },
            status_code=201,
        )

    return app


if __name__ == "__main__":
    run_standalone("ministry", create_app(), config.SERVICES["ministry"]["port"])
