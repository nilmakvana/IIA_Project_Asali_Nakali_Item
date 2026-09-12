"""
Central configuration for the Asali/Nakali (genuine / counterfeit) item
identification system.

The project is 5 independent processes. By default they all run on this
machine on localhost (for development / single-laptop grading):

    manufacturer-service   :8081   ->  data/manufacturer.db
    distributor-service    :8082   ->  data/distributor.db
    vendor-service         :8083   ->  data/vendor.db
    ministry-service       :8084   ->  data/ministry.db
    mediator + GUI         :8080   ->  no DB of its own, only talks HTTP

For the real distributed deployment (see DEPLOYMENT.md) each service runs on
its OWN physical/virtual machine. To point at that deployment, copy
sources.example.json -> sources.json in the project root and fill in the LAN
IP of the machine each service runs on:

    {
      "manufacturer": {"host": "192.168.1.11", "port": 8081},
      "distributor":  {"host": "192.168.1.12", "port": 8082},
      "vendor":       {"host": "192.168.1.13", "port": 8083},
      "ministry":     {"host": "192.168.1.14", "port": 8084}
    }

sources.json is machine-specific and is NOT committed (see .gitignore) -
every machine in the deployment gets its own copy pointing at the same 4 IPs.
Any single entry can also be overridden with an environment variable, e.g.
    ASALI_MANUFACTURER_HOST=192.168.1.11 ASALI_MANUFACTURER_PORT=8081

Each of the four data-source databases was "designed in isolation": different
table names, different column names, different naming conventions (snake_case,
camelCase, PascalCase-ish).  The ONE thing they share is the *value* of the
item's unique code, stored under a different attribute name in every source:

    manufacturer.manufactured_batches.gtin_serial
    distributor.distributed_stock.item_code
    vendor.purchaseScans.productBarcode
    ministry.counterfeit_reports.suspect_code   (and verified_genuine_registry.auth_code)
"""

import json
import os
import socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")

# Loopback default - used when no sources.json / env override is present,
# i.e. the plain single-machine "python start.py" mode.
HOST = "127.0.0.1"

# What a standalone service (python -m datasources.xxx_service, or the
# mediator run directly) binds to. 0.0.0.0 = reachable from other machines
# on the LAN, which is what the distributed deployment needs.
BIND_HOST = os.environ.get("ASALI_BIND_HOST", "0.0.0.0")

# NB: ports are in the 8080s, not 5000s - macOS ControlCenter (AirPlay
# Receiver) squats on :5000 and returns 403 to everything.
MEDIATOR_PORT = 8080

SERVICES = {
    "manufacturer": {"port": 8081, "db": os.path.join(DATA_DIR, "manufacturer.db")},
    "distributor":  {"port": 8082, "db": os.path.join(DATA_DIR, "distributor.db")},
    "vendor":       {"port": 8083, "db": os.path.join(DATA_DIR, "vendor.db")},
    "ministry":     {"port": 8084, "db": os.path.join(DATA_DIR, "ministry.db")},
}

# Colour codes used consistently across the GUI so every fact keeps its
# provenance (which source it came from).
SOURCE_COLOURS = {
    "manufacturer": "#3b82f6",
    "distributor":  "#a855f7",
    "vendor":       "#14b8a6",
    "ministry":     "#f59e0b",
}


def _load_source_overrides() -> dict:
    if not os.path.exists(SOURCES_FILE):
        # Common typo/misplacement guard: warn if something *close* to
        # sources.json exists right next to it, since a wrong filename here
        # silently falls back to 127.0.0.1 with no other symptom than
        # "it just won't connect to the other machine".
        try:
            near_misses = [
                f for f in os.listdir(BASE_DIR)
                if f != "sources.example.json"
                and f.lower() in ("source.json", "sources.json.txt", "source.json.txt")
            ]
        except OSError:
            near_misses = []
        if near_misses:
            print(f"[config] WARNING: no {SOURCES_FILE} found, but {near_misses} "
                  f"exists in the same folder - rename it to exactly 'sources.json' "
                  f"(project root, next to config.py). Until then, every source "
                  f"defaults to 127.0.0.1.")
        return {}
    try:
        with open(SOURCES_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[config] WARNING: found {SOURCES_FILE} but could not parse it "
              f"({exc}) - every source will default to 127.0.0.1 until this is "
              f"fixed. Check it's valid JSON (e.g. paste it into jsonlint.com).")
        return {}
    unknown = [k for k in data if k != "_comment" and k not in SERVICES]
    if unknown:
        print(f"[config] WARNING: {SOURCES_FILE} has unrecognised key(s) {unknown} "
              f"- expected some of {list(SERVICES)}. Typo? These entries are "
              f"ignored, so that source will default to 127.0.0.1.")
    return data


_OVERRIDES = _load_source_overrides()


def service_host(name: str) -> str:
    """Where to *reach* the named service - possibly on another machine."""
    env = os.environ.get(f"ASALI_{name.upper()}_HOST")
    if env:
        return env
    return _OVERRIDES.get(name, {}).get("host", HOST)


def service_port(name: str) -> int:
    env = os.environ.get(f"ASALI_{name.upper()}_PORT")
    if env:
        return int(env)
    return int(_OVERRIDES.get(name, {}).get("port", SERVICES[name]["port"]))


def service_url(name: str) -> str:
    return f"http://{service_host(name)}:{service_port(name)}"


def using_remote_sources() -> bool:
    """True once sources.json (or an env override) points anywhere off localhost."""
    return any(service_host(n) not in ("127.0.0.1", "localhost") for n in SERVICES)


def lan_ip() -> str:
    """
    Best-effort guess at this machine's LAN IP, for the startup banner.

    This asks the OS which interface it would use to reach the internet,
    which is normally Wi-Fi/Ethernet - but if a VPN is active, the OS often
    routes everything through the VPN tunnel instead, and this returns the
    tunnel's address (typically a 10.x.x.x range) instead of your actual
    Wi-Fi IP. That address is usually NOT reachable from another machine on
    the same Wi-Fi, which breaks the multi-machine deployment in confusing
    ways (services look fine locally, but check_network.py fails on the
    other machine).

    If that happens: find your real Wi-Fi IP yourself
    (macOS: `ipconfig getifaddr en0`, Windows: `ipconfig`, Linux:
    `hostname -I`) and either use it directly in sources.json, or force this
    function to report it with:  ASALI_LAN_IP=<that ip> python start.py ...
    """
    env = os.environ.get("ASALI_LAN_IP")
    if env:
        return env
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def lan_ip_caveat() -> str | None:
    """
    A one-line heads-up to print next to lan_ip() when it looks like it may
    have picked up a VPN tunnel instead of the real Wi-Fi/Ethernet address
    (common giveaway: a 10.x.x.x address while a VPN client is running).
    Not definitive - just worth a second look before trusting it.
    """
    if os.environ.get("ASALI_LAN_IP"):
        return None  # user already forced it, nothing to warn about
    ip = lan_ip()
    if ip.startswith("10."):
        return (
            "this looks like it could be a VPN tunnel address, not your real "
            "Wi-Fi IP - if the other machine can't reach it, compare against "
            "`ipconfig getifaddr en0` (macOS) / `ipconfig` (Windows) / "
            "`hostname -I` (Linux) and use that instead, or set "
            "ASALI_LAN_IP=<correct ip>"
        )
    return None


# Codes pre-loaded for the GUI drop-down / demo.  The label intentionally does
# NOT reveal whether the item is genuine or fake - that is what the system
# computes.
DEMO_CODES = [
    ("SLP-AMOX500-B2401-0007",  "Amoxil-SL 500 Capsule"),
    ("SLP-PARA650-B2405-0012",  "Parasafe 650 Tablet"),
    ("APX-AZITH250-B2312-0003", "Azispan 250 Tablet"),
    ("MRC-ORS-B2407-0071",      "OrsKind ORS Powder"),
    ("SLP-PANTO40-B2402-0021",  "Pantosure 40 Tablet"),
    ("MRC-DOLO650-B2403-0044",  "Dolokind 650 Tablet"),
    ("SLP-VITD3-B2404-0050",    "D-Rise SL Sachet"),
    ("APX-COUGH100-B2311-0009", "CoughEase Syrup 100ml"),
    ("SLP-INSUL-B2406-0033",    "InsuSL 100IU Injection"),
    ("MRC-METFOR-B2405-0066",   "Metfokind 500 Tablet"),
    ("ZZZ-CIPRO500-FAKE-9001",  "Cipro 500 Tablet"),
    ("ZZZ-REMDES-FAKE-9099",    "Remdes 100 Vial"),
]
