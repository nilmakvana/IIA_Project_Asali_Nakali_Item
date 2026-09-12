"""Vendor / retail-POS data source (vendors + purchase scans + customer sales)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from datasources.base import make_service, run_standalone  # noqa: E402


def create_app():
    return make_service("vendor", config.SERVICES["vendor"]["db"])


if __name__ == "__main__":
    run_standalone("vendor", create_app(), config.SERVICES["vendor"]["port"])
