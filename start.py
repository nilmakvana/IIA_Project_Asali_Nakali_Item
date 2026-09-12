"""
Launcher. Supports several deployment shapes (see DEPLOYMENT.md for the full
4-machine walkthrough, DEPLOYMENT_TWO_MACHINE.md for a 2-database-per-machine
split):

    python start.py                          # ALL-IN-ONE: build DBs, start all
                                              # 5 services on this machine, open
                                              # browser. (single-laptop grading)

    python start.py --services-only          # start all 4 data sources (no
                                              # mediator), bound to the network.

    python start.py --mediator-only          # start only the GUI, reading
                                              # sources.json for where the data
                                              # sources live.

    python start.py --services=A,B           # start only the NAMED data
                                              # sources (any subset of
                                              # manufacturer/distributor/
                                              # vendor/ministry), bound to the
                                              # network, no mediator.

    python start.py --services=A,B --with-mediator
                                              # same as above, PLUS also start
                                              # the mediator/GUI on this same
                                              # machine - this is how you make
                                              # "any machine can be the
                                              # mediator": add --with-mediator
                                              # to whichever machine you want
                                              # to demo the GUI from.

Other flags:
    --rebuild       force-rebuild this machine's local databases first
    --no-browser    don't auto-open the browser
    --lan           bind every locally-started server to 0.0.0.0 (implied by
                     --services-only / --mediator-only / --services=...; add
                     it to the plain all-in-one mode too if you want OTHER
                     machines to be able to open this machine's GUI)

Example - split the 4 databases 2-and-2 across two machines, either one able
to run the GUI (see DEPLOYMENT_TWO_MACHINE.md):

    Machine A:  python start.py --services=manufacturer,distributor --with-mediator
    Machine B:  python start.py --services=vendor,ministry --with-mediator

Ctrl+C stops everything this process started.
"""
import os
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
import uvicorn

import config
from data.build_databases import build_one

_SERVICE_MODULES = {
    "manufacturer": "datasources.manufacturer_service",
    "distributor": "datasources.distributor_service",
    "vendor": "datasources.vendor_service",
    "ministry": "datasources.ministry_service",
}


class ServerThread(threading.Thread):
    """Runs one ASGI app (a FastAPI service) in its own thread, each with its
    own asyncio event loop via uvicorn.Server."""

    def __init__(self, name, app, host, port):
        super().__init__(daemon=True)
        self.name = name
        cfg = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._srv = uvicorn.Server(cfg)

    def run(self):
        self._srv.run()

    def stop(self):
        self._srv.should_exit = True


def _wait_healthy(targets, timeout=20):
    """targets: {label: url-to-poll}"""
    deadline = time.time() + timeout
    pending = set(targets)
    while pending and time.time() < deadline:
        for name in list(pending):
            try:
                if requests.get(targets[name], timeout=2).status_code < 500:
                    pending.discard(name)
                    print(f"  ready: {name}")
            except Exception:
                pass
        if pending:
            time.sleep(0.4)
    if pending:
        print(f"  [warn] not reachable in time: {', '.join(sorted(pending))}")
    return not pending


def _parse_args(argv):
    services_arg = None
    for a in argv:
        if a.startswith("--services="):
            services_arg = a.split("=", 1)[1]
    flags = {a for a in argv if not a.startswith("--services=")}
    return {
        "services_arg": services_arg,
        "services_only": "--services-only" in flags,
        "mediator_only": "--mediator-only" in flags,
        "with_mediator": "--with-mediator" in flags,
        "rebuild": "--rebuild" in flags,
        "no_browser": "--no-browser" in flags,
        "lan": "--lan" in flags,
    }


def main():
    a = _parse_args(sys.argv[1:])

    if a["mediator_only"]:
        requested = []
    elif a["services_arg"] is not None:
        requested = [s.strip() for s in a["services_arg"].split(",") if s.strip()]
        bad = [s for s in requested if s not in config.SERVICES]
        if bad:
            sys.exit(f"unknown service(s) {bad} in --services=... - "
                      f"expected some of {list(config.SERVICES)}")
    else:
        requested = list(config.SERVICES)  # --services-only, or plain all-in-one

    run_mediator = (
        a["mediator_only"]
        or a["with_mediator"]
        or (a["services_arg"] is None and not a["services_only"])
    )
    lan = a["lan"] or a["services_only"] or a["mediator_only"] or a["services_arg"] is not None
    bind_host = config.BIND_HOST if lan else "127.0.0.1"

    for name in requested:
        db_path = config.SERVICES[name]["db"]
        if a["rebuild"] or not os.path.exists(db_path):
            print(f"Building {name}'s isolated database ...")
            build_one(name)

    servers = []
    health_targets = {}

    for name in requested:
        module = __import__(_SERVICE_MODULES[name], fromlist=["create_app"])
        port = config.SERVICES[name]["port"]
        servers.append(ServerThread(name, module.create_app(), bind_host, port))
        health_targets[name] = f"http://127.0.0.1:{port}/health"

    if run_mediator:
        from mediator.app import create_app as mediator_app

        servers.append(ServerThread("mediator", mediator_app(), bind_host, config.MEDIATOR_PORT))
        health_targets["mediator"] = f"http://127.0.0.1:{config.MEDIATOR_PORT}/"

    for s in servers:
        s.start()

    print("\nStarting ...")
    _wait_healthy(health_targets)

    print()
    for name in requested:
        where = "this machine" if lan else "localhost only"
        print(f"  {name:12s}  http://{config.lan_ip() if lan else '127.0.0.1'}:"
              f"{config.SERVICES[name]['port']}   ({where})")
    gui = None
    if run_mediator:
        gui_host = config.lan_ip() if lan else "127.0.0.1"
        gui = f"http://{gui_host}:{config.MEDIATOR_PORT}/"
        print(f"  {'GUI':12s}  {gui}")
        if not config.using_remote_sources() and not requested:
            print("\n  [warn] no local services and no sources.json found - copy "
                  "sources.example.json to sources.json and fill in the other "
                  "machine(s)' IPs (see DEPLOYMENT.md / DEPLOYMENT_TWO_MACHINE.md).")
    if lan:
        caveat = config.lan_ip_caveat()
        if caveat:
            print(f"\n  [note] {caveat}")

    if gui and not a["no_browser"]:
        try:
            webbrowser.open(gui)
        except Exception:
            pass

    print("\nRunning. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping ...")
        for s in servers:
            s.stop()


if __name__ == "__main__":
    main()
