"""
Creates and populates the four *independently designed* databases.

Run directly:      python data/build_databases.py
Or via launcher:   python start.py --rebuild

Design rules honoured here (from the project brief):
  * Each DB is designed in isolation - different table + column names and
    different naming conventions.
  * At least one column carries the SAME VALUE across every source even though
    the attribute name differs (the item's unique code, and also company /
    supplier / vendor names, and price).
  * Data is seeded with 12 deliberate scenarios so the federated verdict engine
    has something interesting to say (see SCENARIOS note at bottom).
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402


def _connect(path):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


# --------------------------------------------------------------------------- #
#  1. MANUFACTURER  (companies register genuine products + batches)           #
#     convention: snake_case, "..._id" primary keys                          #
# --------------------------------------------------------------------------- #
def build_manufacturer(path):
    con = _connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE companies (
            company_id      INTEGER PRIMARY KEY,
            company_name    TEXT NOT NULL,
            license_no      TEXT NOT NULL,
            country         TEXT,
            established_year INTEGER
        );
        CREATE TABLE products (
            product_id   INTEGER PRIMARY KEY,
            company_id   INTEGER NOT NULL REFERENCES companies(company_id),
            brand_name   TEXT NOT NULL,
            generic_name TEXT NOT NULL,
            dosage_form  TEXT,
            strength     TEXT,
            hsn_code     TEXT
        );
        CREATE TABLE manufactured_batches (
            batch_id        INTEGER PRIMARY KEY,
            product_id      INTEGER NOT NULL REFERENCES products(product_id),
            gtin_serial     TEXT NOT NULL UNIQUE,   -- << shared code value
            mfg_date        TEXT NOT NULL,
            expiry_date     TEXT NOT NULL,
            batch_qty       INTEGER NOT NULL,
            mrp             REAL NOT NULL,
            factory_location TEXT
        );
        """
    )

    companies = [
        (1, "Sun Life Pharma",    "MFG-LIC-MH-1188", "India", 1994),
        (2, "Apex Remedies",      "MFG-LIC-GJ-4471", "India", 2001),
        (3, "Medi Remedies Corp", "MFG-LIC-KA-2093", "India", 1987),
    ]
    products = [
        (1, 1, "Amoxil-SL",  "Amoxicillin",       "Capsule",   "500 mg",     "3004"),
        (2, 1, "Parasafe",   "Paracetamol",       "Tablet",    "650 mg",     "3004"),
        (3, 2, "Azispan",    "Azithromycin",      "Tablet",    "250 mg",     "3004"),
        (4, 1, "Pantosure",  "Pantoprazole",      "Tablet",    "40 mg",      "3004"),
        (5, 3, "Dolokind",   "Paracetamol",       "Tablet",    "650 mg",     "3004"),
        (6, 1, "D-Rise SL",  "Cholecalciferol",   "Sachet",    "60000 IU",   "3004"),
        (7, 2, "CoughEase",  "Dextromethorphan",  "Syrup",     "100 ml",     "3004"),
        (8, 1, "InsuSL",     "Insulin Glargine",  "Injection", "100 IU/ml",  "3004"),
        (9, 3, "Metfokind",  "Metformin",         "Tablet",    "500 mg",     "3004"),
        (10, 3, "OrsKind",   "Oral Rehydration Salts", "Powder", "21.8 g",   "2106"),
    ]
    #  batch_id, product_id, gtin_serial, mfg, expiry, qty, mrp, factory
    batches = [
        (1,  1, "SLP-AMOX500-B2401-0007", "2024-01-10", "2026-12-31", 200,  128.50, "Mumbai Unit-2"),
        (2,  2, "SLP-PARA650-B2405-0012", "2024-05-04", "2027-04-30", 500,   32.00, "Mumbai Unit-1"),
        (3,  3, "APX-AZITH250-B2312-0003", "2023-12-01", "2026-11-30", 150,  96.00, "Ahmedabad Unit-1"),
        (4,  4, "SLP-PANTO40-B2402-0021", "2024-02-15", "2027-01-31", 300,   88.00, "Mumbai Unit-2"),
        (5,  5, "MRC-DOLO650-B2403-0044", "2024-03-20", "2026-10-31", 250,   30.00, "Bengaluru Unit-1"),
        (6,  6, "SLP-VITD3-B2404-0050",   "2024-04-12", "2026-09-30",  50,  210.00, "Mumbai Unit-1"),
        (7,  7, "APX-COUGH100-B2311-0009", "2023-11-05", "2026-03-15", 180,  75.00, "Ahmedabad Unit-2"),
        (8,  8, "SLP-INSUL-B2406-0033",   "2024-06-01", "2027-05-31", 120, 1450.00, "Mumbai Unit-3"),
        (9,  9, "MRC-METFOR-B2405-0066",  "2024-05-22", "2026-12-31", 400,   45.00, "Bengaluru Unit-1"),
        (10, 10, "MRC-ORS-B2407-0071",    "2024-07-09", "2027-06-30", 1000,  22.00, "Bengaluru Unit-2"),
        # NOTE: no batch for ZZZ-CIPRO500-FAKE-9001 or ZZZ-REMDES-FAKE-9099
        #       -> "ghost codes" that never left a real factory.
    ]
    c.executemany("INSERT INTO companies VALUES (?,?,?,?,?)", companies)
    c.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", products)
    c.executemany("INSERT INTO manufactured_batches VALUES (?,?,?,?,?,?,?,?)", batches)
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  2. DISTRIBUTOR / SUPPLIER  (buys from companies, sells to vendors)         #
#     convention: snake_case, abbreviated prefixes (sup_, dist_)             #
# --------------------------------------------------------------------------- #
def build_distributor(path):
    con = _connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE suppliers (
            sup_id          INTEGER PRIMARY KEY,
            sup_name        TEXT NOT NULL,
            drug_license    TEXT NOT NULL,
            gstin           TEXT,
            region          TEXT,
            contact_email   TEXT,
            blacklist_flag  INTEGER NOT NULL DEFAULT 0,
            blacklist_reason TEXT
        );
        CREATE TABLE inbound_consignments (
            consignment_id  INTEGER PRIMARY KEY,
            sup_id          INTEGER NOT NULL REFERENCES suppliers(sup_id),
            source_company  TEXT NOT NULL,          -- << shared: manufacturer name
            received_on     TEXT NOT NULL,
            invoice_no      TEXT
        );
        CREATE TABLE distributed_stock (
            dist_row_id      INTEGER PRIMARY KEY,
            consignment_id   INTEGER NOT NULL REFERENCES inbound_consignments(consignment_id),
            item_code        TEXT NOT NULL,         -- << shared code value
            product_desc     TEXT,
            qty_supplied     INTEGER NOT NULL,
            unit_price       REAL NOT NULL,         -- << shared-ish: wholesale price
            supplied_to_vendor TEXT NOT NULL,       -- << shared: vendor name
            dispatch_date    TEXT NOT NULL
        );
        """
    )
    suppliers = [
        (1, "MediReach Distributors", "DL-DIST-2201", "27ABCDE1234F1Z5", "West",  "ops@medireach.example",   0, None),
        (2, "HealthLine Supplies",    "DL-DIST-3310", "29HLINE5678G1Z2", "South", "desk@healthline.example",  0, None),
        (3, "QuickPharma Traders",    "DL-DIST-5540", "27QPT9999H1Z9",   "West",  "sales@quickpharma.example", 0, None),
    ]
    #  consignment_id, sup_id, source_company, received_on, invoice
    consignments = [
        (1, 1, "Sun Life Pharma",    "2024-02-01", "INV-1001"),
        (2, 2, "Apex Remedies",      "2024-01-15", "INV-2001"),
        (3, 1, "Medi Remedies Corp", "2024-04-05", "INV-1002"),
        (4, 3, "Apex Remedies",      "2024-07-01", "INV-3002"),  # used for the INSUL mismatch
        (5, 1, "Sun Life Pharma",    "2024-04-20", "INV-1003"),
    ]
    #  dist_row_id, consignment_id, item_code, desc, qty, unit_price, vendor, dispatch
    stock = [
        (1, 1, "SLP-AMOX500-B2401-0007", "Amoxil-SL 500mg Capsule", 60,  100.00, "CityCare Chemist",   "2024-02-05"),
        (2, 1, "SLP-PARA650-B2405-0012", "Parasafe 650 Tablet",     120,  24.00, "CityCare Chemist",   "2024-05-10"),
        (3, 2, "APX-AZITH250-B2312-0003", "Azispan 250 Tablet",      40,  72.00, "Wellness Pharmacy",  "2024-01-20"),
        (4, 5, "SLP-PANTO40-B2402-0021", "Pantosure 40 Tablet",      80,  66.00, "QuickMeds 24x7",     "2024-04-25"),
        (5, 3, "MRC-DOLO650-B2403-0044", "Dolokind 650 Tablet",      90,  22.00, "CityCare Chemist",   "2024-04-10"),
        (6, 1, "SLP-VITD3-B2404-0050",   "D-Rise SL Sachet",         30, 160.00, "CityCare Chemist",   "2024-04-20"),
        (7, 5, "SLP-VITD3-B2404-0050",   "D-Rise SL Sachet",         40, 160.00, "Wellness Pharmacy",  "2024-04-22"),
        (8, 2, "APX-COUGH100-B2311-0009", "CoughEase Syrup 100ml",   60,  60.00, "Wellness Pharmacy",  "2023-12-01"),
        (9, 4, "SLP-INSUL-B2406-0033",   "InsuSL 100IU Injection",   30, 1300.00, "QuickMeds 24x7",    "2024-07-05"),
        (10, 3, "MRC-METFOR-B2405-0066", "Metfokind 500 Tablet",    150,  38.00, "Wellness Pharmacy",  "2024-06-01"),
        (11, 3, "MRC-ORS-B2407-0071",    "OrsKind Powder",          300,  18.00, "CityCare Chemist",   "2024-07-15"),
        # no distribution row for the two ZZZ ghost codes
    ]
    c.executemany("INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?)", suppliers)
    c.executemany("INSERT INTO inbound_consignments VALUES (?,?,?,?,?)", consignments)
    c.executemany("INSERT INTO distributed_stock VALUES (?,?,?,?,?,?,?,?)", stock)
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  3. VENDOR / RETAIL POS  (scans code at purchase, sells to customer)       #
#     convention: camelCase, "...Id" primary keys                           #
# --------------------------------------------------------------------------- #
def build_vendor(path):
    con = _connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE vendors (
            vendorId     INTEGER PRIMARY KEY,
            vendorName   TEXT NOT NULL,            -- << shared: vendor name
            shopLicense  TEXT NOT NULL,
            city         TEXT,
            pincode      TEXT
        );
        CREATE TABLE purchaseScans (
            scanId          INTEGER PRIMARY KEY,
            vendorId        INTEGER NOT NULL REFERENCES vendors(vendorId),
            productBarcode  TEXT NOT NULL,         -- << shared code value
            scannedLabelName TEXT,
            packSize        TEXT,
            sellingPrice    REAL,                  -- << shared-ish: retail price
            scanTimestamp   TEXT NOT NULL,
            cashierNote     TEXT
        );
        CREATE TABLE customerSales (
            saleId          INTEGER PRIMARY KEY,
            scanId          INTEGER NOT NULL REFERENCES purchaseScans(scanId),
            saleTime        TEXT NOT NULL,
            billNo          TEXT,
            customerPhoneHash TEXT
        );
        """
    )
    vendors = [
        (1, "CityCare Chemist",  "RET-CHEM-8801", "Pune",      "411001"),
        (2, "QuickMeds 24x7",    "RET-CHEM-9912", "Mumbai",    "400001"),
        (3, "Wellness Pharmacy", "RET-CHEM-7723", "Bengaluru", "560001"),
    ]
    #  scanId, vendorId, productBarcode, label, pack, price, ts, note
    scans = [
        (1,  1, "SLP-AMOX500-B2401-0007", "AMOXIL SL 500 CAP",  "10x10", 125.00, "2024-02-08 09:30", "shelf A3"),
        (2,  1, "SLP-PARA650-B2405-0012", "PARASAFE 650",       "15x10",  30.00, "2024-05-12 11:05", ""),
        (3,  3, "APX-AZITH250-B2312-0003", "AZISPAN 250 TAB",   "3x1",    94.00, "2024-01-25 16:20", ""),
        (4,  2, "SLP-PANTO40-B2402-0021", "PANTOSURE 40",       "10x10",  85.00, "2024-04-28 10:10", "new supplier"),
        (5,  2, "MRC-DOLO650-B2403-0044", "DOLOKIND 650",       "15x10",  28.00, "2024-04-15 12:00", "walk-in stock"),
        (6,  1, "SLP-VITD3-B2404-0050",   "D RISE SL SACHET",   "1x4",   205.00, "2024-04-25 13:45", ""),
        (7,  3, "SLP-VITD3-B2404-0050",   "D-RISE SL",          "1x4",   205.00, "2024-04-27 09:15", ""),
        (8,  3, "APX-COUGH100-B2311-0009", "COUGHEASE SYRUP",   "100ml",  72.00, "2023-12-05 15:30", ""),
        (9,  2, "SLP-INSUL-B2406-0033",   "INSUSL 100IU INJ",   "1x1",  1400.00, "2024-07-08 17:00", "cold chain"),
        (10, 3, "MRC-METFOR-B2405-0066",  "METFOKIND 500",      "20x10",  42.00, "2024-06-05 10:40", ""),
        (11, 1, "MRC-ORS-B2407-0071",     "ORSKIND POWDER",     "1x1",    21.00, "2024-07-18 14:20", ""),
        (12, 2, "ZZZ-CIPRO500-FAKE-9001", "CIPRO 500 TAB",      "10x10",  60.00, "2024-05-01 11:30", "cheap lot"),
        (13, 3, "ZZZ-REMDES-FAKE-9099",   "REMDES 100 VIAL",    "1x1",  3500.00, "2024-08-01 18:10", "urgent buy"),
    ]
    c.executemany("INSERT INTO vendors VALUES (?,?,?,?,?)", vendors)
    c.executemany("INSERT INTO purchaseScans VALUES (?,?,?,?,?,?,?,?)", scans)

    # ---- customer sales: {scanId: (first_sale_date, count)} ----------------
    plan = {
        1: ("2024-02-15", 3),
        2: ("2024-05-20", 5),
        3: ("2024-02-05", 2),
        4: ("2024-05-10", 4),
        5: ("2024-04-25", 3),
        6: ("2024-05-01", 60),   # D-Rise batch qty is only 50  -> over-issue
        7: ("2024-05-05", 45),   # + these  -> 105 sold vs 50 made  (cloned codes)
        8: ("2023-12-20", 3),
        9: ("2024-07-15", 2),
        10: ("2024-06-15", 3),
        11: ("2024-07-25", 4),
        12: ("2024-05-10", 3),
        13: ("2024-08-10", 2),
    }
    # a genuine batch (CoughEase) that keeps getting sold *after* it expired
    special = [(8, "2026-06-20", "BILL-EXP-0001"), (8, "2026-07-30", "BILL-EXP-0002")]

    sales = []
    sale_id = 1
    for scan_id, (start, n) in sorted(plan.items()):
        d0 = datetime.strptime(start, "%Y-%m-%d")
        for i in range(n):
            d = (d0 + timedelta(days=5 * i)).strftime("%Y-%m-%d")
            ph = f"ph_{((scan_id * 1000 + i) * 2654435761) & 0xFFFFFFFF:08x}"
            sales.append((sale_id, scan_id, d, f"BILL-{scan_id:02d}-{i+1:03d}", ph))
            sale_id += 1
    for scan_id, d, bill in special:
        sales.append((sale_id, scan_id, d, bill, f"ph_{sale_id:08x}"))
        sale_id += 1

    c.executemany("INSERT INTO customerSales VALUES (?,?,?,?,?)", sales)
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  4. MINISTRY OF CONSUMER AFFAIRS  (counterfeit registry + enforcement)     #
#     convention: snake_case, "..._ref" business keys                        #
# --------------------------------------------------------------------------- #
def build_ministry(path):
    con = _connect(path)
    c = con.cursor()
    c.executescript(
        """
        CREATE TABLE counterfeit_reports (
            report_ref              TEXT PRIMARY KEY,
            suspect_code            TEXT NOT NULL,   -- << shared code value
            reported_by_vendor      TEXT,            -- << shared: vendor name
            reported_against_supplier TEXT,          -- << shared: supplier name
            complaint_date          TEXT NOT NULL,
            lab_result              TEXT,            -- COUNTERFEIT | GENUINE | PENDING
            status                  TEXT,            -- CONFIRMED | OPEN | CLOSED
            remarks                 TEXT,
            filed_by                TEXT DEFAULT 'field-inspector'
        );
        CREATE TABLE verified_genuine_registry (
            reg_id              INTEGER PRIMARY KEY,
            auth_code           TEXT NOT NULL UNIQUE,  -- << shared code value
            verifying_authority TEXT,
            verified_on         TEXT
        );
        CREATE TABLE enforcement_actions (
            action_id       INTEGER PRIMARY KEY,
            report_ref      TEXT NOT NULL REFERENCES counterfeit_reports(report_ref),
            action_taken    TEXT,
            penalty_amount  REAL,
            action_date     TEXT
        );
        """
    )
    reports = [
        ("MOCA-2025-0001", "SLP-PANTO40-B2402-0021", "QuickMeds 24x7", "MediReach Distributors",
         "2025-02-10", "COUNTERFEIT", "CONFIRMED",
         "Sub-standard active ingredient; packaging clone confirmed by Central Drugs Lab.", "field-inspector"),
        ("MOCA-2025-0002", "ZZZ-CIPRO500-FAKE-9001", "QuickMeds 24x7", "QuickPharma Traders",
         "2025-03-05", "COUNTERFEIT", "CONFIRMED",
         "No manufacturer of record for this code; spurious drug.", "field-inspector"),
        ("MOCA-2025-0007", "MRC-METFOR-B2405-0066", "Wellness Pharmacy", "MediReach Distributors",
         "2025-06-18", "PENDING", "OPEN",
         "Consumer complaint: unusual taste and colour. Sample sent for testing.", "consumer-helpline"),
    ]
    registry = [
        (1, "SLP-AMOX500-B2401-0007", "CDSCO", "2024-02-01"),
        (2, "SLP-PARA650-B2405-0012", "CDSCO", "2024-05-20"),
        (3, "MRC-ORS-B2407-0071",     "CDSCO", "2024-07-20"),
        (4, "SLP-VITD3-B2404-0050",   "CDSCO", "2024-04-15"),
        (5, "SLP-INSUL-B2406-0033",   "CDSCO", "2024-06-10"),
        (6, "APX-COUGH100-B2311-0009", "CDSCO", "2023-11-20"),
        (7, "MRC-METFOR-B2405-0066",  "CDSCO", "2024-05-30"),
        # deliberately NOT registered: APX-AZITH250 (genuine but unlisted),
        # MRC-DOLO650 (custody break), SLP-PANTO40 (lab counterfeit).
    ]
    actions = [
        (1, "MOCA-2025-0001", "Batch recall ordered; supplier licence suspended 90 days", 500000, "2025-03-01"),
        (2, "MOCA-2025-0002", "FIR under Drugs & Cosmetics Act; stock seized", 250000, "2025-03-20"),
    ]
    c.executemany("INSERT INTO counterfeit_reports VALUES (?,?,?,?,?,?,?,?,?)", reports)
    c.executemany("INSERT INTO verified_genuine_registry VALUES (?,?,?,?)", registry)
    c.executemany("INSERT INTO enforcement_actions VALUES (?,?,?,?,?)", actions)
    con.commit()
    con.close()


_BUILDERS = {
    "manufacturer": build_manufacturer,
    "distributor": build_distributor,
    "vendor": build_vendor,
    "ministry": build_ministry,
}


def build_one(name, verbose=True):
    """Build a single source's database - e.g. on a machine that will only
    ever run that one data-source service (see DEPLOYMENT.md)."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _BUILDERS[name](config.SERVICES[name]["db"])
    if verbose:
        cfg = config.SERVICES[name]
        size = os.path.getsize(cfg["db"])
        print(f"  built {name:12s} -> {os.path.relpath(cfg['db'])}  ({size/1024:.1f} KB)")


def build_all(verbose=True):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    for name in _BUILDERS:
        build_one(name, verbose=verbose)


# ----------------------------------------------------------------------------
#  SCENARIOS baked into the seed data (what the verdict engine should catch)
# ----------------------------------------------------------------------------
#  SLP-AMOX500-B2401-0007  ASALI    clean chain, in verified registry
#  SLP-PARA650-B2405-0012  ASALI    clean chain, in verified registry
#  APX-AZITH250-B2312-0003 ASALI    clean chain, NOT in registry (registry optional)
#  MRC-ORS-B2407-0071      ASALI    clean chain, in verified registry
#  SLP-PANTO40-B2402-0021  NAKALI   ministry lab result = COUNTERFEIT
#  MRC-DOLO650-B2403-0044  NAKALI   custody break (supplied to CityCare, sold at QuickMeds)
#  SLP-VITD3-B2404-0050    NAKALI   over-issue: batch qty 50, 105 units sold (cloned code)
#  APX-COUGH100-B2311-0009 NAKALI   sold after expiry date (2026-03-15)
#  SLP-INSUL-B2406-0033    SUSPECT  distribution source company != manufacturer of record
#  MRC-METFOR-B2405-0066   SUSPECT  open ministry investigation, lab result PENDING
#  ZZZ-CIPRO500-FAKE-9001  NAKALI   ghost code - never manufactured (+ confirmed report)
#  ZZZ-REMDES-FAKE-9099    NAKALI   ghost code - never manufactured, NOT yet reported
#                                   -> GUI can file the report back to the Ministry
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    # python data/build_databases.py            -> build all 4 (single-machine mode)
    # python data/build_databases.py --only X    -> build just source X's DB
    #                                                (e.g. copying this project to a
    #                                                machine that will only run
    #                                                datasources/X_service.py)
    if "--only" in sys.argv:
        target = sys.argv[sys.argv.index("--only") + 1]
        if target not in _BUILDERS:
            sys.exit(f"unknown source '{target}', expected one of {list(_BUILDERS)}")
        print(f"Building {target}'s isolated database ...")
        build_one(target)
    else:
        print("Building isolated databases ...")
        build_all()
    print("done.")
