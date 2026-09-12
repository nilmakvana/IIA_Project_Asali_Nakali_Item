"""
Distributor / supplier data source.

Adds one *write* endpoint on top of the generic read API so that the Ministry
service can push an event back into this source (source-to-source communication):

    POST /flag-supplier   {supplier_name, reason, report_ref}
        -> sets blacklist_flag = 1 on the matching supplier row
"""
import os
import sqlite3
import sys

from fastapi import Body
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from datasources.base import make_service, run_standalone  # noqa: E402

DB = config.SERVICES["distributor"]["db"]


def create_app():
    app = make_service("distributor", DB)

    @app.post("/flag-supplier")
    def flag_supplier(body: dict = Body(default={})):
        name = (body.get("supplier_name") or "").strip()
        reason = body.get("reason") or "Flagged following a Ministry counterfeit report"
        ref = body.get("report_ref", "")
        if not name:
            return JSONResponse({"error": "supplier_name is required"}, status_code=400)

        con = sqlite3.connect(DB)
        try:
            cur = con.execute(
                "UPDATE suppliers SET blacklist_flag = 1, "
                "blacklist_reason = ? WHERE sup_name = ?",
                (f"{reason} (ref {ref})".strip(), name),
            )
            con.commit()
            affected = cur.rowcount
        finally:
            con.close()
        return {
            "source": "distributor",
            "action": "flag-supplier",
            "supplier_name": name,
            "rows_updated": affected,
            "matched": affected > 0,
        }

    return app


if __name__ == "__main__":
    run_standalone("distributor", create_app(), config.SERVICES["distributor"]["port"])
