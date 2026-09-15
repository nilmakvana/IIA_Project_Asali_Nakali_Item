"""
Mediator + GUI.

Has no database of its own.  Every page is assembled by calling the four
data-source REST APIs through integration/federation.py.
"""
import json
import os
import sys

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from integration import federation, schema_matcher  # noqa: E402
from integration.mediated_schema import GAV_MAPPING, MEDIATED_VIEWS  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))


def create_app():
    app = FastAPI(title="Asali / Nakali mediator")
    static_dir = os.path.join(_HERE, "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))
    templates.env.globals["SOURCE_COLOURS"] = config.SOURCE_COLOURS
    templates.env.filters["tojson"] = lambda obj, **kw: Markup(json.dumps(obj, **kw))
    # cache-busts /static/* whenever a file changes, so browsers never serve a
    # stale stylesheet/script after an edit + restart
    try:
        newest_mtime = max(os.path.getmtime(os.path.join(static_dir, f)) for f in os.listdir(static_dir))
        templates.env.globals["STATIC_VERSION"] = str(int(newest_mtime))
    except (OSError, ValueError):
        templates.env.globals["STATIC_VERSION"] = "0"

    def render(name: str, request: Request, **ctx):
        return templates.TemplateResponse(
            request, name,
            {"url_for": request.url_for, "current_path": request.url.path, **ctx},
        )

    # ----------------------------- pages ------------------------------- #
    @app.get("/", response_class=HTMLResponse, name="home")
    def home(request: Request):
        return render("index.html", request, demo_codes=config.DEMO_CODES)

    @app.get("/item/{code:path}", response_class=HTMLResponse, name="item_page")
    def item_page(request: Request, code: str):
        try:
            rec = federation.verify(code)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"federation failed: {exc}") from exc
        return render("item.html", request, rec=rec, demo_codes=config.DEMO_CODES)

    @app.get("/lab", response_class=HTMLResponse, name="lab_page")
    def lab_page(request: Request):
        health = federation.source_health()
        down_sources = [h["source"] for h in health if h["status"] != "up"]
        report = None
        error = None
        try:
            schemas, samples = federation.gather_schema_and_samples()
            report = schema_matcher.analyze(schemas, samples)
        except Exception as exc:  # noqa: BLE001 - unexpected failure, not just a down source
            error = federation.friendly_error(exc)
        return render(
            "lab.html", request, health=health, down_sources=down_sources,
            report=report, error=error, gav=GAV_MAPPING, views=MEDIATED_VIEWS,
        )

    @app.get("/query", response_class=HTMLResponse, name="query_page")
    def query_page(request: Request):
        return render("query.html", request, views=MEDIATED_VIEWS, demo_codes=config.DEMO_CODES)

    @app.get("/explorer", response_class=HTMLResponse, name="explorer_page")
    def explorer_page(request: Request):
        return render("explorer.html", request)

    # ----------------------------- JSON API ---------------------------- #
    @app.get("/api/verify/{code:path}")
    def api_verify(code: str):
        return federation.verify(code)

    @app.get("/api/sources")
    def api_sources():
        return federation.source_health()

    @app.get("/api/schema-match")
    def api_schema_match():
        schemas, samples = federation.gather_schema_and_samples()
        return schema_matcher.analyze(schemas, samples)

    @app.post("/api/sql")
    def api_sql(body: dict = Body(default={})):
        return federation.run_sql(body.get("sql", ""))

    @app.post("/api/report/{code:path}")
    def api_report(code: str):
        result = federation.file_report(code)
        status = 200 if result.get("ok", True) else 502
        return JSONResponse(content=result, status_code=status)

    @app.get("/api/explorer/schema")
    def api_explorer_schema():
        return federation.explorer_schema()

    @app.get("/api/explorer/rows")
    def api_explorer_rows(source: str, table: str, limit: int = 50):
        return federation.explorer_rows(source, table, limit)

    return app


if __name__ == "__main__":
    import uvicorn

    print("\nmediator + GUI")
    print(f"  local:  http://127.0.0.1:{config.MEDIATOR_PORT}")
    print(f"  LAN:    http://{config.lan_ip()}:{config.MEDIATOR_PORT}  "
          f"<-- open this from any machine on the network")
    if config.using_remote_sources():
        print("  data sources (from sources.json):")
        for n in config.SERVICES:
            print(f"    {n:12s} {config.service_url(n)}")
    else:
        print("  data sources: all on localhost (no sources.json found) - "
              "see DEPLOYMENT.md to point at other machines")
    caveat = config.lan_ip_caveat()
    if caveat:
        print(f"  [note] {caveat}")
    print()
    uvicorn.run(create_app(), host=config.BIND_HOST, port=config.MEDIATOR_PORT, log_level="info")
