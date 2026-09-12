# Project A — Evaluation report

*Identification of Asali (genuine) or Nakali (counterfeit) items.*
Point-by-point against the Part-A rubric.

---

## 1. Scope of work — *(1 mark)*

**In scope**

* Four relational data sources, each **designed in isolation** (different table
  names, column names and naming conventions), modelling:
  1. **item unique code + description** — captured at the point of purchase
     (`vendor.purchaseScans`) and by the manufacturer (`manufacturer.manufactured_batches`);
  2. **distributor / supplier details and what they distribute to vendors**
     (`distributor.suppliers`, `inbound_consignments`, `distributed_stock`);
  3. **item details by manufacturer (company) with unique code**
     (`manufacturer.companies`, `products`, `manufactured_batches`);
  4. **reports to the Ministry of Consumer Affairs** for Nakali items
     (`ministry.counterfeit_reports`, `verified_genuine_registry`, `enforcement_actions`).
* A **REST API in front of each database** (§5), and a mediator that answers
  every question purely by calling those four APIs (§6) - never touching a
  database file directly.
* A **schema-matching** step that discovers the common attribute across the four
  isolated schemas, feeding a small **mediated (global) schema**.
* **Deployment across four different machines**, one database per machine,
  with the mediator/GUI startable on any one of them — not just four ports on
  one laptop (§7, `DEPLOYMENT.md`).
* A **rule-based authenticity engine** producing an ASALI / SUSPECT / NAKALI
  verdict + trust score + explained red flags + provenance timeline
  (the "complete history view" the brief asks for).
* **Write-back**: filing a counterfeit report to the Ministry, and an
  event that propagates from the Ministry to the distributor.
* A **GUI** that presents the integrated result.

**Out of scope** (explicitly): real GS1/DSCSA or blockchain integration,
authentication/RBAC, production-scale tuning, ML-based schema matching, physical
barcode-scanner hardware (codes are typed/selected), and any real government
system — the "Ministry" is a local service.

**Assumptions**: each database is administered separately and will not expose its
tables to the others; the item code is printed on the pack and is the same
physical string everywhere it is recorded; the mediator is trusted.

---

## 2. New / innovative aspects — *(1 mark)*

1. **The join key is discovered, not given.** The four schemas share no foreign
   key. `integration/schema_matcher.py` uses **instance-based matching** (Jaccard
   overlap of actual column values) to prove that
   `gtin_serial ≡ item_code ≡ productBarcode ≡ suspect_code ≡ auth_code`, and it
   **rejects a look-alike**: `license_no` / `drug_license` / `shopLicense` have
   very similar names but disjoint values, so they are *not* merged.
2. **Counterfeit detection as a federated query, not a lookup.** A code is Nakali
   not only when a lab says so, but when the *integrated* picture is
   inconsistent: a **ghost code** never manufactured; **quantity over-issue**
   (more units sold than the batch ever contained → the code was cloned); a
   **broken chain of custody** (sold at an outlet the distributor never
   supplied); **source-company mismatch**; **sale after expiry**. None of these
   is visible in any single source.
3. **Explainable trust score.** Every rule carries a signed score impact, so the
   verdict is auditable rather than a black box.
4. **Closed enforcement loop.** NAKALI → one click files a Ministry report →
   Ministry service calls the distributor service to **blacklist the supplier**.
   Source-to-source communication triggered by an integrated verdict.
5. **Live query-plan visualisation.** Every screen can show exactly which SQL ran
   on which source and how long it took — the decomposition is a first-class,
   inspectable artefact.
6. **One command, fully offline.** `python start.py` runs the whole
   microservice mesh; only `FastAPI`, `uvicorn` and `requests` are needed.
   Every API also gets free interactive docs at `/docs` on its own port
   (FastAPI generates an OpenAPI schema from the code automatically).
7. **Actually distributed, not simulated - and location-transparent.** The
   same codebase runs as one process on a laptop, as 4 independent services
   on 4 machines, or split 2-and-2 across 2 machines, with a
   location-transparent mediator (`sources.json` + `service_url()`) that
   never needs to know which sources are local and which are remote.
   Flipping between shapes needs no code change - just which flags you run
   where - and **any machine can pick up the mediator/GUI role** on top of
   whatever it already runs (`--with-mediator`), verified in §7.1 by
   actually swapping the role between machines and re-checking every
   verdict. See `DEPLOYMENT.md` / `DEPLOYMENT_TWO_MACHINE.md`.

---

## 3. Databases schema design — *(2 marks)*

Four SQLite databases, created in
[`data/build_databases.py`](data/build_databases.py). Designed in isolation — the
naming conventions deliberately differ.

### 3.1 `manufacturer.db` — *snake_case, `*_id` surrogate keys*
```
companies(company_id PK, company_name, license_no, country, established_year)
products(product_id PK, company_id FK→companies, brand_name, generic_name,
         dosage_form, strength, hsn_code)
manufactured_batches(batch_id PK, product_id FK→products,
         gtin_serial UNIQUE,           -- ← shared code value
         mfg_date, expiry_date, batch_qty, mrp, factory_location)
```

### 3.2 `distributor.db` — *snake_case, abbreviated prefixes (`sup_`, `dist_`)*
```
suppliers(sup_id PK, sup_name, drug_license, gstin, region, contact_email,
          blacklist_flag, blacklist_reason)
inbound_consignments(consignment_id PK, sup_id FK→suppliers,
          source_company,               -- ← shared: manufacturer name
          received_on, invoice_no)
distributed_stock(dist_row_id PK, consignment_id FK→inbound_consignments,
          item_code,                     -- ← shared code value
          product_desc, qty_supplied,
          unit_price,                    -- ← shared-ish: wholesale price
          supplied_to_vendor,            -- ← shared: vendor name
          dispatch_date)
```

### 3.3 `vendor.db` — *camelCase, `*Id` keys*
```
vendors(vendorId PK, vendorName, shopLicense, city, pincode)
purchaseScans(scanId PK, vendorId FK→vendors,
          productBarcode,                -- ← shared code value
          scannedLabelName, packSize,
          sellingPrice,                  -- ← shared-ish: retail price
          scanTimestamp, cashierNote)
customerSales(saleId PK, scanId FK→purchaseScans, saleTime, billNo,
          customerPhoneHash)
```

### 3.4 `ministry.db` — *snake_case, `*_ref` business keys*
```
counterfeit_reports(report_ref PK,
          suspect_code,                  -- ← shared code value
          reported_by_vendor,            -- ← shared: vendor name
          reported_against_supplier,     -- ← shared: supplier name
          complaint_date, lab_result, status, remarks, filed_by)
verified_genuine_registry(reg_id PK,
          auth_code UNIQUE,              -- ← shared code value
          verifying_authority, verified_on)
enforcement_actions(action_id PK, report_ref FK→counterfeit_reports,
          action_taken, penalty_amount, action_date)
```

### 3.5 The required "same value, different name" columns

| Meaning | manufacturer | distributor | vendor | ministry |
|---|---|---|---|---|
| **item unique code** | `gtin_serial` | `item_code` | `productBarcode` | `suspect_code` / `auth_code` |
| company / manufacturer name | `companies.company_name` | `inbound_consignments.source_company` | — | `reported_against_supplier`* |
| supplier name | — | `suppliers.sup_name` | — | `reported_against_supplier` |
| vendor name | — | `distributed_stock.supplied_to_vendor` | `vendors.vendorName` | `reported_by_vendor` |
| price | `manufactured_batches.mrp` | `distributed_stock.unit_price` | `purchaseScans.sellingPrice` | — |

The item code is the primary integration key; the name columns give the matcher
several secondary correspondences to find.

---

## 4. Populating the data — *(2 marks)*

All in [`data/build_databases.py`](data/build_databases.py):
3 companies, 10 products, 10 genuine batches, 3 suppliers, 5 consignments,
11 distribution rows, 3 vendors, 13 purchase scans, ~140 customer sales,
3 counterfeit reports, 7 verified-registry entries, 2 enforcement actions.

**12 deliberate scenarios** so the federated engine has something to prove
(asserted in `tests/test_federation.py`):

| Code | Expected | Why |
|---|---|---|
| `SLP-AMOX500-B2401-0007` | **ASALI** | clean chain, in verified registry |
| `SLP-PARA650-B2405-0012` | **ASALI** | clean chain, in verified registry |
| `APX-AZITH250-B2312-0003` | **ASALI** | clean chain, **not** in registry (registration is voluntary) |
| `MRC-ORS-B2407-0071` | **ASALI** | clean chain, in verified registry |
| `SLP-PANTO40-B2402-0021` | **NAKALI** | Ministry lab result = `COUNTERFEIT` |
| `MRC-DOLO650-B2403-0044` | **NAKALI** | chain of custody broken (supplied to CityCare, sold at QuickMeds) |
| `SLP-VITD3-B2404-0050` | **NAKALI** | batch qty 50, but 105 units distributed/sold → cloned code |
| `APX-COUGH100-B2311-0009` | **NAKALI** | sold after expiry date |
| `SLP-INSUL-B2406-0033` | **SUSPECT** | distributor's `source_company` ≠ manufacturer of record |
| `MRC-METFOR-B2405-0066` | **SUSPECT** | open Ministry investigation, lab result `PENDING` |
| `ZZZ-CIPRO500-FAKE-9001` | **NAKALI** | ghost code — no manufacturer batch (+ confirmed report) |
| `ZZZ-REMDES-FAKE-9099` | **NAKALI** | ghost code — no batch, **not yet reported** → GUI files it |

---

## 5. APIs creation for data fetching — *(2 marks)*

### 5.1 One REST API per data source — `datasources/base.py`, built on FastAPI

Each isolated DB becomes an independent **FastAPI** micro-service exposing the
same contract. Because it's FastAPI, every one of the four APIs also gets a
free interactive explorer at `http://<host>:<port>/docs` (Swagger UI, built
from an auto-generated OpenAPI schema) - useful for demoing "API creation"
directly to the professor, one source at a time, without the GUI involved.

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness + per-table row counts |
| `GET /schema` | `{table: [{name, type}]}` |
| `GET /sample?limit=` | `{table: {column: [values]}}` — feeds the matcher |
| `POST /query` | whitelisted, parameterised `SELECT` — returns rows **and the SQL it executed** |

`POST /query` grammar: `{table, columns, where:{col:{op,value}}, limit}` with
`op ∈ {=,!=,<,>,<=,>=,LIKE,IN}`. Table and column names are validated against the
live schema, so it is safe against injection and cannot touch another table.

### 5.2 Two write endpoints, for the closed enforcement loop

* Ministry: `POST /reports` — files a new counterfeit report.
* Distributor: `POST /flag-supplier` — blacklists a supplier (called by the
  Ministry service itself, not by the mediator - see §7).

### 5.3 Where each API actually runs — one machine per source

`datasources/*_service.py` are standalone processes: `python -m
datasources.manufacturer_service` starts only that one API, bound to
`0.0.0.0` so it is reachable from other machines on the network, not just
`localhost`. In the distributed deployment (**[DEPLOYMENT.md](DEPLOYMENT.md)**)
each of the 4 APIs runs on its own physical machine with only its own
database file on disk — it has no access to the other three.

### 5.4 Schema matching that feeds the API layer — `integration/schema_matcher.py`

Hybrid similarity for every **cross-source** column pair:

```
score = 0.30 · name_similarity      (tokenise: splitCamelCase + snake_case,
                                     synonym map, Jaccard on tokens + difflib ratio)
      + 0.10 · type_similarity      (inferred: date / integer / number / text)
      + 0.60 · value_similarity     (Jaccard of the actual sample value sets)   ← dominant
match  ⇔  score ≥ 0.34
```

* **Instance-based (value) matching dominates** — that is what identifies the
  code columns even though the names differ.
* **Surrogate-key guard**: two small-integer id columns get their value
  similarity discounted ×0.1 unless the names also agree, so row-ids don't all
  collapse into one bogus cluster.
* **Union-Find** groups matched pairs into clusters of semantically equal
  columns.
* The **global join key** = the largest cluster containing the code columns.

Result on the seed data (`GET /api/schema-match`, shown on `/lab`):

```
cluster 5  {gtin_serial, item_code, productBarcode, suspect_code, auth_code}   ← GLOBAL JOIN KEY
cluster 3  {supplied_to_vendor, reported_by_vendor, vendorName}
cluster 2  {source_company, company_name}
cluster 2  {sup_name, reported_against_supplier}
NOT merged {license_no, drug_license}   (similar names, disjoint values)
```

### 5.5 Mediated schema (GAV) — `integration/mediated_schema.py`

The discovered correspondences are curated into a **Global-As-View** map: each
mediated attribute (`code`, `product_name`, `company`, `mrp`, …) is defined as an
expression over the local sources. Two virtual views are published:
`item_master` (the complete-history summary) and `item_authenticity` (the
verdict). Rendered as a table on `/lab`.

---

## 6. Querying data sources through APIs — *(2 marks)*

`integration/federation.py` — the mediator holds no data and no database
connection of any kind; every fact it uses is fetched by calling the 4 REST
APIs from §5.

### 6.1 Point query — one item, all 4 APIs

`verify(code)` calls the APIs in three rounds (each round parallel, via
`ThreadPoolExecutor`, since each machine answers independently):

* **Round 1** (5 calls): `POST /query` on all four services, filtered by the
  item code.
* **Round 2** (dependent calls): the ids returned by round 1 are used to call
  `products`, `inbound_consignments`, `customerSales`, `vendors`,
  `enforcement_actions` via `WHERE … IN (…)`.
* **Round 3**: `companies` and `suppliers`, keyed off round 2's results.
* **Integrate**: the JSON rows from all the calls are assembled in Python into
  one `item_master` record, a provenance timeline, and a value-conflict list —
  joined on the discovered code key.

Every API call - which service, which endpoint, what came back, how long it
took - is recorded and shown on the item page under **"Query decomposition &
federation plan"** (≈11 API calls across 4 sources for a typical code).

### 6.2 Mediated SQL front-end — `/query` screen

`run_sql()` accepts
`SELECT <cols> FROM item_master | item_authenticity [WHERE code = '…'] [LIMIT n]`
and answers it purely by calling the 4 APIs:

* **`WHERE code = '…'`** → the 4-API point lookup above.
* **no `WHERE`** → a lighter 2-API path (`manufacturer` + `ministry` only),
  joined on the code in the mediator. The response states which path was
  taken and why the result is less complete than the full verdict.

---

## 7. Establishing the communication between the data sources — *(2 marks)*

### 7.1 The databases run on different machines - any machine can also become the mediator

**The general principle, independent of any specific topology**: each of the
4 databases can run on *any* machine, in *any* combination of how many
machines are used and which databases each one holds - the code has no
built-in notion of "4 machines" or "2 machines", only "here are the services
I run locally" (`--services=<names>`) and "here is where to find everything
else" (`sources.json`). Layered on top, *any* machine - including one with
none of the four databases - can additionally serve the mediator/GUI, either
by adding `--with-mediator` to what it already runs, or via a dedicated
`--mediator-only` process on a separate machine. Nothing about "which machine
is the mediator" is fixed in the design; it is simply whichever machine you
last pointed a browser at.

Two fully-documented topologies, both with the property that **any machine
in the deployment can add the GUI role on top of whatever it already
runs**:

**4 machines, one database each** — full walkthrough (exact commands,
firewall notes, IP discovery): **[DEPLOYMENT.md](DEPLOYMENT.md)**.

```
Machine 1: manufacturer.db  ← python start.py --services=manufacturer   (:8081)
Machine 2: distributor.db   ← python start.py --services=distributor    (:8082)
Machine 3: vendor.db        ← python start.py --services=vendor         (:8083)
Machine 4: ministry.db      ← python start.py --services=ministry       (:8084)
```

Any one of the four becomes the mediator too by adding one flag to its own
command — no 5th machine required:

```
python start.py --services=manufacturer --with-mediator   # e.g. Machine 1
```

(A dedicated, database-free 5th/Nth machine can instead run
`python start.py --mediator-only`, reading every entry from its own
`sources.json` — useful if you *do* have a spare machine and want the GUI
machine to host nothing.)

**2 machines, two databases each** — full walkthrough:
**[DEPLOYMENT_TWO_MACHINE.md](DEPLOYMENT_TWO_MACHINE.md)**.

```
Machine A: manufacturer.db + distributor.db  ← python start.py --services=manufacturer,distributor
Machine B: vendor.db + ministry.db           ← python start.py --services=vendor,ministry
```

Same mechanism: either machine adds `--with-mediator` to its own command to
also serve the GUI.

**What makes this possible without any code change per role:**

* Each service binds to `0.0.0.0` (not just `127.0.0.1`) so it is reachable
  from other machines on the LAN.
* `sources.json` (one per machine, gitignored, templated by
  `sources.example.json`) tells that machine's processes where to find
  whichever services *aren't* local to it — by **LAN IP**, e.g.
  `192.168.1.11:8081` — while anything not listed there falls back to
  `127.0.0.1` automatically, which is exactly right for a service the same
  machine hosts itself. `config.py`'s `service_url()` resolves it (env var →
  file → localhost default), so the mediator code never needs to know or
  care whether a given source is local or remote.
* `check_network.py` round-trips `GET /health` to every configured address
  before a demo, so a Wi-Fi/firewall problem is caught before the professor
  is looking at the screen.

**Verified across every shape, not just designed.** Each of the following was
actually run, not only reasoned about:

* **4 machines, one database each, one of them also the mediator.** Started
  4 separate single-service processes; added `--with-mediator` to the
  manufacturer process and confirmed `SLP-PANTO40-B2402-0021` resolved
  correctly across all 4 sources (NAKALI, `LAB_COUNTERFEIT`); moved
  `--with-mediator` to the ministry process instead and confirmed
  `MRC-DOLO650-B2403-0044` resolved correctly too (NAKALI, `CUSTODY_BREAK`),
  with `tests/test_federation.py`'s full 12-scenario suite passing against
  that live topology both times.
* **2 machines, two databases each, either as mediator** — the same role
  swap, verified in both directions.
* **3 machines, an uneven 2+1+1 split** (one machine hosting manufacturer +
  distributor, the other two hosting vendor and ministry alone) — verified
  with the 2-DB machine as mediator, then again with a 1-DB machine as
  mediator, both giving correct verdicts and both passing the full test
  suite.
* **4 machines, one database each, plus a genuinely separate 5th machine
  running `--mediator-only` with zero local databases** — `check_network.py`
  on that 5th machine resolved and reached all four remote sources, and a
  full lookup (`APX-COUGH100-B2311-0009` → NAKALI, `EXPIRED_AT_SALE`) worked
  correctly with no service running locally at all.

That last case also closes what used to be a caveat here: a genuinely
database-free mediator machine isn't just a theoretical option, it has been
exercised end-to-end.

### 7.2 What the mediator talks to what, and why

All inter-service traffic is **JSON over HTTP**; no process can read another's
database file directly - only through its REST API.

| From → To | Call | Trigger |
|---|---|---|
| Mediator → every source | `POST /query` | any verification / SQL query (parallel) |
| Mediator → all sources | `GET /health`, `/schema`, `/sample` | `/lab` page, startup health-gate, `check_network.py` |
| Mediator → **Ministry** | `POST /reports` | user clicks "Report to Ministry" on a NAKALI item — **writes a new counterfeit report back into `ministry.db`**, auto-generating `MOCA-YYYY-NNNN` |
| **Ministry → Distributor** | `POST /flag-supplier` | a report with `lab_result = COUNTERFEIT` → the Ministry *service itself* (not the mediator) calls the distributor service, which sets `blacklist_flag = 1` on the named supplier — genuine source-to-source communication, not mediator-brokered |

`start.py` blocks until every `/health` is green before opening the GUI, so
the mesh is verified connected on every launch.

### 7.3 Operational robustness - misconfiguration and downed sources fail loud, not silent

Distributing across real machines surfaces a class of bug that a single
laptop never sees: a wrong IP, a typo'd flag, a service that isn't up yet.
Each of these used to fail *silently* (falling back to `127.0.0.1` with no
explanation) during actual multi-machine testing, so they were fixed to fail
loud instead:

* **`sources.json` problems are reported at startup** (`config.py`): a
  missing file with a near-miss name next to it (e.g. `source.json` instead
  of `sources.json`), invalid JSON, or an unrecognised key (`Vendor` vs
  `vendor`) each print a specific `[config] WARNING` explaining exactly what
  to fix - previously all three cases just silently defaulted to localhost.
* **`check_network.py` shows its work**: the exact absolute path it read,
  which services are overridden vs. defaulting to `127.0.0.1`, before it
  even attempts a connection.
* **`start.py` validates its own flags**: an unrecognised or mistyped flag
  (`--service=...` instead of `--services=...`, a space instead of `=`) now
  exits immediately with the list of valid flags, instead of silently
  running the plain-localhost default and looking like it "worked".
* **A `BIND MODE:` banner** is the first thing every launch prints -
  `network (0.0.0.0)` or `localhost only (127.0.0.1)` - so it's never
  ambiguous whether this machine is actually reachable from the others.
* **A downed source never crashes the mediator with an opaque error**: e.g.
  filing a report when the Ministry service is unreachable used to bubble up
  an unhandled connection exception → FastAPI's generic
  `500 Internal Server Error` (plain text, not JSON) → the browser's
  `response.json()` throwing a confusing `SyntaxError`. `federation.py`'s
  `file_report()` now catches that and returns a structured
  `{"ok": false, "error": "..."}` (HTTP 502), and the GUI shows the actual
  reason in place.
* **No Python-version landmine on a real lab machine**: two spots used
  `str | None` (Python 3.10+ only syntax); on an older default `python3`
  (e.g. Ubuntu 20.04's 3.8) this would `TypeError` at import, before the
  service even starts. Replaced with `typing.Optional[str]`, portable back
  to Python 3.5+.

Every one of these was found and fixed by actually running the distributed
deployment end-to-end - a real VPN-confused `lan_ip()` guess, a real Ubuntu
machine reachable only via its LAN IP, a real downed Ministry service - not
only reasoned about in the abstract.

---

## 8. Query-results integration + GUI — *(3 marks)*

`mediator/` — a FastAPI app that **owns no database**; every page is assembled from
the four REST services.

### `/item/<code>` — the "complete history (view)" the brief asks for
* **Verdict banner** — ASALI / SUSPECT / NAKALI, headline, and a **trust-score
  gauge** (0–100).
* **Red flags** — every triggered rule with severity, plain-English explanation
  and signed score impact.
* **Provenance timeline** — manufactured → registered → distributed → scanned →
  sold → reported → enforcement, each event colour-coded by its **source of
  record** so provenance is never lost.
* **Integrated record** — the mediated `item_master` tuple (manufacturer,
  licence, factory, dates, MRP, batch qty, distributed qty, suppliers, vendors,
  registry status, report count).
* **Value conflicts across the isolated sources** — where the same mediated
  attribute has different values in different sources, classified
  *representational* (MRP vs wholesale vs retail — benign) vs *semantic
  mismatch* (company of record disagrees — red flag).
* **Federation plan** — the full decomposition (source, SQL, rows, ms).
* **Report to Ministry of Consumer Affairs** button (for non-ASALI items) —
  fires the write-back + supplier-blacklist loop and shows the new report ref.

### `/lab` — Integration lab
Live data-source table; the schema-matching **similarity-matrix heat-map**;
discovered clusters incl. the flagged **GLOBAL JOIN KEY**; the **GAV mediated
schema** map; the source-to-source communication summary.

### `/query` — Federated SQL
Type SQL against the mediated views, run it, and watch the decomposition into
per-source sub-queries and the re-integrated result. Preset buttons for the
point-lookup (4 sources), the multi-row join (2 sources) and the authenticity
view.

### Design and UX

A dedicated pass beyond "it works", since the rubric weighs the GUI on its
own (3 marks, the largest single line item):

* **Light, professional visual system** — a single consistent palette (ink /
  muted / accent-blue, plus the three verdict colours) applied uniformly
  across banners, cards, tables and chips; a custom shield-check mark as the
  product's logo rather than a generic icon or emoji.
* **Provenance never loses colour-coding** — every fact on the item page
  carries a `src-chip` in that source's colour (blue/purple/teal/amber),
  reused identically on `/lab`'s data-source table and the homepage's
  "how it works" strip, so the same visual language means "which of the 4
  independent sources this came from" everywhere in the app.
* **Active-state navigation and focus-visible outlines** — the current page
  is highlighted in the top nav; every interactive element (links, buttons,
  inputs) has a keyboard-visible focus ring, not just a mouse hover state.
* **A homepage architecture strip** — four colour-coded steps
  (manufacturer → distributor → retail vendor → ministry) with connecting
  arrows, so the "four independent sources" premise is shown, not just
  described in a paragraph, before the user even runs a query.
* **Responsive down to a phone-width viewport** — the top bar collapses its
  subtitle and the architecture strip switches from a horizontal row to a
  vertical flow with rotated arrows below ~760px, rather than clipping or
  requiring horizontal scroll.
* **Failure states are designed, not default-browser-ugly** — a downed
  source shows a specific red flag or a plain-English error inline (see
  §7.3), never a raw stack trace or an unstyled JSON blob.
* **Cache-busted static assets** (`STATIC_VERSION` derived from file mtime)
  so a CSS/JS change is guaranteed to show up on next reload instead of
  risking a stale browser cache during iteration or a live demo.

---

## How to demo

### Quick version, one laptop (functionality)

1. `python start.py` → GUI opens on the homepage - point out the
   manufacturer→distributor→vendor→ministry strip before typing anything.
2. **`SLP-AMOX500-B2401-0007`** → ASALI 100/100, clean timeline, 4 sources, 0 flags.
3. **`SLP-VITD3-B2404-0050`** → NAKALI 30/100, `QUANTITY_OVER_ISSUE` (50 made,
   105 sold). Open the API call plan — 11 calls, 4 sources.
4. **`MRC-DOLO650-B2403-0044`** → NAKALI, `CUSTODY_BREAK` — sold at QuickMeds,
   only ever supplied to CityCare.
5. **`ZZZ-REMDES-FAKE-9099`** → NAKALI, `GHOST_CODE`. Click **Report to
   Ministry** → new `MOCA-2026-xxxx`; re-run to see it now carries an open report.
6. **`/lab`** → similarity matrix: the 5 code columns light up; the licence
   columns stay dark (correctly not merged).
7. **`/query`** → run the two presets; compare the 4-API vs 2-API plans.
8. `python tests/test_federation.py` → all scenarios pass.

### Full version, distributed (the actual requirement)

4 machines, one database each: **[DEPLOYMENT.md](DEPLOYMENT.md)**. Only 2
machines: **[DEPLOYMENT_TWO_MACHINE.md](DEPLOYMENT_TWO_MACHINE.md)** (2
databases each). In short: each machine runs `python start.py
--services=<its own source(s)>`, one machine adds `--with-mediator` to also
serve the GUI (or a spare machine runs `--mediator-only`), `check_network.py`
confirms all four sources are reachable first, then repeat steps 2–7 above
from the browser — every step now fetches live across the network. Add
`--with-mediator` to a *different* machine's command instead and the GUI
comes up there with identical results — that swap is exactly what §7.1
verifies.
