# Asali / Nakali — Identification of genuine vs. counterfeit items

> Project 2 — *Identification of fake items (medicine) while purchasing.*
> Given the unique code on a medicine pack, return its **complete history**:
> is it genuine (**Asali**) or fake (**Nakali**), when was it procured, and who
> was the distributor / supplier — by integrating four databases that were
> **designed in isolation** and, in the real deployment, run on **four
> different machines**.

---

## 1. What it does (the use case)

A pharmacist scans the code `SLP-VITD3-B2404-0050` at the counter. In one click
the system:

1. queries **four independent data sources** — the manufacturer, the
   distributor, the retail vendor, and the Ministry of Consumer Affairs — each
   its own database, each reachable only through its own REST API;
2. joins their answers on the item code (a key it had to **discover**, because
   every source stores it under a different attribute name);
3. reconstructs the full **provenance timeline** (manufactured → registered →
   distributed → scanned in → sold → reported);
4. runs a rule engine and returns a verdict — **ASALI / SUSPECT / NAKALI** — with
   a trust score and an explained list of red flags;
5. if the item is fake and not yet on record, lets the user **file a report back
   to the Ministry**, which in turn **blacklists the supplier** in the
   distributor's database.

## 2. Architecture

```
                 ┌─────────────────────────────────────────────┐
   Browser ───►  │   Mediator + GUI            :8080            │
                 │   - schema matching                          │
                 │   - query decomposition via the source APIs  │
                 │   - counterfeit rule engine                  │
                 └───┬───────────┬───────────┬───────────┬──────┘
     HTTP/JSON API ┌──┘        ┌──┘        ┌──┘        ┌──┘
                    ▼           ▼           ▼           ▼
          manufacturer   distributor    vendor     ministry     ← 4 isolated
             :8081          :8082        :8083       :8084         services
               │              │            │           │
          manufacturer.db  distributor.db vendor.db  ministry.db  ← 4 SQLite DBs
```

Every box is a **separate process** speaking REST. No service can read another
service's database file; the mediator owns no data at all. In the demo you can
run all 5 boxes on your laptop, or spread them across **4 real machines** — see
**[DEPLOYMENT.md](DEPLOYMENT.md)**.

## 3. Run it (single machine, for development)

```bash
pip install -r requirements.txt
python start.py
```

`start.py` builds the four databases (if missing), starts all five services,
waits for every `/health` to go green, and opens
<http://127.0.0.1:8080>. `Ctrl+C` stops everything.

Flags: `--rebuild` (force fresh data), `--no-browser`.

Run the scenario tests in a second terminal:

```bash
python tests/test_federation.py        # or:  python -m pytest -q
```

## 4. Run it distributed

Only have **2 machines**? Follow **[DEPLOYMENT_TWO_MACHINE.md](DEPLOYMENT_TWO_MACHINE.md)**
instead — the 4 databases split 2-and-2 across the two machines, and
**either machine can run the mediator/GUI** on demand (`--with-mediator`).

Have **4 machines**, one database each — the setup that matches the rubric's
"databases on different machines" most literally? Full step-by-step guide:
**[DEPLOYMENT.md](DEPLOYMENT.md)**.

Short version (4-machine variant):

```bash
# on each of 4 machines, run just that one service:
python data/build_databases.py --only manufacturer
python -m datasources.manufacturer_service      # repeat with distributor / vendor / ministry

# on the machine that will run the GUI (any 5th machine, or one of the 4):
cp sources.example.json sources.json            # then fill in the 4 LAN IPs
python check_network.py                         # sanity-check connectivity first
python start.py --mediator-only
```

Because every machine carries the same project and the same `sources.json`,
the GUI can be started on **any one of them** and it will fetch and join data
live from the other machines over the network — which is what lets the
professor run the query from whichever machine they're sitting at.

### The three screens

| URL | What you see |
|-----|--------------|
| `/` → `/item/<code>` | verdict banner, trust gauge, red flags, provenance timeline, integrated record, value-conflict panel, the full API call plan, "report to Ministry" button |
| `/lab` | live status of the 4 data-source APIs, schema-matching similarity matrix + discovered clusters, the mediated-schema map |
| `/query` | write SQL against the mediated view; watch it get resolved into calls to the source APIs and re-integrated |

## 5. The four isolated schemas

Each DB uses a different naming convention. The **only** thing they share is the
*value* of the item code (and, secondarily, company / supplier / vendor names
and price) — stored under a different attribute name everywhere:

| Source | table.column holding the shared code | convention |
|--------|--------------------------------------|-----------|
| manufacturer | `manufactured_batches.gtin_serial` | `snake_case`, `*_id` keys |
| distributor | `distributed_stock.item_code` | `snake_case`, abbreviated prefixes |
| vendor | `purchaseScans.productBarcode` | `camelCase`, `*Id` keys |
| ministry | `counterfeit_reports.suspect_code`, `verified_genuine_registry.auth_code` | `snake_case`, `*_ref` business keys |

Full DDL + seed data: [`data/build_databases.py`](data/build_databases.py).

## 6. How each rubric item is satisfied

See [`REPORT.md`](REPORT.md) for the point-by-point mapping with file
pointers. In brief:

| # | Rubric item | Where |
|---|-------------|-------|
| 1 | Scope of work | `REPORT.md` §1 |
| 2 | New / innovative aspects | `REPORT.md` §2 |
| 3 | Database schema design | `data/build_databases.py` |
| 4 | Populating the data | `data/build_databases.py` (12 designed scenarios) |
| 5 | APIs creation for data fetching | `datasources/base.py` (+ per-source extra endpoints) |
| 6 | Querying data sources through APIs | `integration/federation.py`, `/query` screen |
| 7 | Communication between the data sources | REST calls between the 4 services + mediator; real multi-machine deployment in `DEPLOYMENT.md` |
| 8 | Results integration + GUI | `mediator/` |

## 7. Layout

```
config.py                     ports, per-machine source locations, demo codes
sources.example.json          template for sources.json (4 machines' LAN IPs)
check_network.py              connectivity check before a distributed demo
DEPLOYMENT.md                 step-by-step: running the 4 DBs on 4 machines
DEPLOYMENT_TWO_MACHINE.md     step-by-step: 2 DBs per machine, either machine can be the GUI
data/build_databases.py       creates + populates the 4 isolated DBs (--only <name> for one)
datasources/
  base.py                     generic REST API (/health /schema /sample /query)
  manufacturer_service.py     :8081
  distributor_service.py      :8082  (+ POST /flag-supplier)
  vendor_service.py           :8083
  ministry_service.py         :8084  (+ GET/POST /reports)
integration/
  schema_matcher.py           hybrid name+type+instance matcher, union-find clustering
  mediated_schema.py          mediated schema mapping
  federation.py               query the source APIs, integrate, run the verdict engine
mediator/
  app.py                      FastAPI app (GUI + JSON API), no DB of its own
  templates/  static/         the 3 screens
start.py                      launcher: all-in-one, --services-only, or --mediator-only
tests/test_federation.py      12 scenario assertions + matcher assertions
```
