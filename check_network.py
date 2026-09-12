"""
Connectivity sanity-check for the distributed deployment.

Run this on the mediator machine (or any machine) BEFORE demoing to the
professor, right after setting up sources.json:

    python check_network.py

It reports, for each of the 4 data sources, whether this machine can reach it
over the network and how many rows are in its tables - i.e. proof that the
service on that OTHER machine is up, its database is populated, and there is
no firewall/Wi-Fi problem between here and there.
"""
import sys
import time

import requests

import config


def main():
    print(f"Reading source locations from: "
          f"{'sources.json' if config.using_remote_sources() else '(none found - using 127.0.0.1)'}\n")

    all_ok = True
    for name in config.SERVICES:
        url = config.service_url(name)
        t0 = time.perf_counter()
        try:
            r = requests.get(f"{url}/health", timeout=4)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            j = r.json()
            counts = j.get("row_counts", {})
            total = sum(counts.values())
            print(f"  OK    {name:12s} {url:28s} {ms:6.1f} ms   {total} rows "
                  f"across {len(counts)} tables")
        except Exception as exc:
            all_ok = False
            print(f"  FAIL  {name:12s} {url:28s} -> {exc}")
            print(f"        check: is 'python -m datasources.{name}_service' running there? "
                  f"same Wi-Fi/LAN? firewall allowing port {config.service_port(name)}?")

    print()
    if all_ok:
        print("All 4 data sources reachable. Safe to run the mediator "
              "(python start.py --mediator-only).")
    else:
        print("One or more sources unreachable - fix before demoing. See DEPLOYMENT.md.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
