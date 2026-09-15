"""
Generic REST wrapper that turns ANY of the four isolated SQLite databases into
an independent HTTP micro-service.

Every source exposes exactly the same 4 read endpoints, which is what makes
federation possible without any of them knowing about the others:

    GET  /health                 -> liveness + per-table row counts
    GET  /schema                 -> {table: [{name, type}, ...]}
    GET  /sample?limit=60        -> {table: {column: [sample values]}}   (for schema matching)
    POST /query   {table, columns, where, limit}  -> parameterised SELECT, returns rows + the SQL it ran

`where` grammar (JSON):
    {"<col>": {"op": "=", "value": "X"}}
    {"<col>": {"op": "IN", "value": ["A", "B"]}}
ops allowed: =  !=  >  <  >=  <=  LIKE  IN
Table + column names are whitelisted against the live schema, so the endpoint
cannot be used for injection or to touch another table.
"""

import sqlite3
import time

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

_ALLOWED_OPS = {"=", "!=", ">", "<", ">=", "<=", "LIKE", "IN"}


def _rows(con, sql, params=()):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def get_schema(db_path):
    """
    {table: [{name, type, pk, nullable, default, references}, ...]}
    references is "<other_table>.<other_column>" when this column is a
    foreign key, else None - pulled straight from SQLite's own catalogue
    (PRAGMA table_info / foreign_key_list), never hand-maintained.
    """
    con = sqlite3.connect(db_path)
    try:
        tables = [
            r["name"]
            for r in _rows(
                con,
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            )
        ]
        schema = {}
        for t in tables:
            info = _rows(con, f'PRAGMA table_info("{t}")')
            fks = _rows(con, f'PRAGMA foreign_key_list("{t}")')
            fk_by_col = {fk["from"]: f'{fk["table"]}.{fk["to"]}' for fk in fks}
            schema[t] = [
                {
                    "name": col["name"],
                    "type": (col["type"] or "TEXT").upper(),
                    "pk": bool(col["pk"]),
                    "nullable": not bool(col["notnull"]),
                    "default": col["dflt_value"],
                    "references": fk_by_col.get(col["name"]),
                }
                for col in info
            ]
        return schema
    finally:
        con.close()


def row_counts(db_path):
    con = sqlite3.connect(db_path)
    try:
        out = {}
        for t in get_schema(db_path):
            out[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        return out
    finally:
        con.close()


def sample_values(db_path, limit=60):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        out = {}
        for t, cols in get_schema(db_path).items():
            rows = con.execute(f'SELECT * FROM "{t}" LIMIT ?', (limit,)).fetchall()
            out[t] = {
                col["name"]: [r[col["name"]] for r in rows if r[col["name"]] not in (None, "")]
                for col in cols
            }
        return out
    finally:
        con.close()


def run_query(db_path, source_name, spec):
    """Build + execute a safe parameterised SELECT.  Returns (payload, http_status)."""
    schema = get_schema(db_path)
    table = (spec or {}).get("table")
    if table not in schema:
        return {"error": f"unknown table '{table}'", "source": source_name}, 400

    valid = {c["name"] for c in schema[table]}
    columns = spec.get("columns") or sorted(valid)
    columns = [c for c in columns if c in valid]
    if not columns:
        return {"error": "no valid columns requested", "source": source_name}, 400

    where = spec.get("where") or {}
    clauses, params = [], []
    for col, cond in where.items():
        if col not in valid:
            return {"error": f"unknown column '{col}'", "source": source_name}, 400
        if not isinstance(cond, dict):
            cond = {"op": "=", "value": cond}
        op = str(cond.get("op", "=")).upper()
        if op not in _ALLOWED_OPS:
            return {"error": f"operator '{op}' not allowed", "source": source_name}, 400
        if op == "IN":
            vals = cond.get("value") or []
            if not isinstance(vals, list) or not vals:
                return {"error": "IN needs a non-empty list", "source": source_name}, 400
            clauses.append(f'"{col}" IN ({",".join("?" * len(vals))})')
            params.extend(vals)
        else:
            clauses.append(f'"{col}" {op} ?')
            params.append(cond.get("value"))

    try:
        limit = max(1, min(int(spec.get("limit", 500)), 5000))
    except (TypeError, ValueError):
        limit = 500

    sql = f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}"'
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" LIMIT {limit}"

    con = sqlite3.connect(db_path)
    try:
        t0 = time.perf_counter()
        rows = _rows(con, sql, params)
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
    finally:
        con.close()

    return {
        "source": source_name,
        "table": table,
        "sql": sql,
        "params": params,
        "elapsed_ms": elapsed,
        "rowcount": len(rows),
        "rows": rows,
    }, 200


def make_service(source_name, db_path):
    app = FastAPI(title=f"{source_name}-data-source")
    app.state.db_path = db_path
    app.state.source_name = source_name

    @app.get("/")
    def index():
        return {
            "service": f"{source_name}-data-source",
            "db": db_path,
            "endpoints": ["/health", "/schema", "/sample", "POST /query"],
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "source": source_name, "row_counts": row_counts(db_path)}

    @app.get("/schema")
    def schema():
        return {"source": source_name, "schema": get_schema(db_path)}

    @app.get("/sample")
    def sample(limit: int = 60):
        return {"source": source_name, "samples": sample_values(db_path, limit)}

    @app.post("/query")
    def query(spec: dict = Body(default={})):
        payload, status = run_query(db_path, source_name, spec or {})
        return JSONResponse(content=payload, status_code=status)

    return app


def run_standalone(source_name, app, port):
    """
    Start this ONE data source bound to all network interfaces, so it is
    reachable from other machines on the LAN - this is what each of the four
    "different machines" in the distributed deployment runs.  See
    DEPLOYMENT.md.
    """
    import uvicorn

    import config

    print(f"\n{source_name} data-source service")
    print(f"  local:  http://127.0.0.1:{port}")
    print(f"  LAN:    http://{config.lan_ip()}:{port}   <-- put this in the other "
          f"machines' sources.json")
    print(f"  health: http://{config.lan_ip()}:{port}/health")
    caveat = config.lan_ip_caveat()
    if caveat:
        print(f"  [note] {caveat}")
    print()
    uvicorn.run(app, host=config.BIND_HOST, port=port, log_level="warning")
