# Distributed deployment — one database per machine

This is what the professor actually runs: the four databases live on **four
different machines** on the same network, a fifth party (the mediator/GUI)
can be started on **any** machine, and it fetches and joins the data live
over HTTP.

Two ways to get there:

* **[A] Real machines** — 4 laptops on the same Wi-Fi, one database each. This
  is the literal, most convincing setup for the viva.
* **[B] 2-machine split** — the 4 databases split 2-and-2 across two
  machines, and *either* machine can run the mediator/GUI on demand. Same
  architecture, easier to arrange if you don't have 4 spare laptops on the
  day. **Only have 2 machines? Use
  [DEPLOYMENT_TWO_MACHINE.md](DEPLOYMENT_TWO_MACHINE.md) instead of this
  section — it walks through exactly this setup in full detail (firewall
  steps, IP discovery, troubleshooting).**

Both use the exact same code — only *where each process runs* changes.

---

## How it works

* Every data source (`manufacturer`, `distributor`, `vendor`, `ministry`) is
  a standalone FastAPI process with its own SQLite file. Nothing about it
  changes when it moves to another machine — it just needs to bind to the
  network instead of only `localhost`, which it already does when you run it
  directly (`python -m datasources.manufacturer_service`).
* The mediator (the GUI) owns **no database**. It only knows the four
  services' addresses, which it reads from `sources.json` — a small file you
  create per machine, e.g.:

  ```json
  {
    "manufacturer": { "host": "192.168.1.11", "port": 8081 },
    "distributor":  { "host": "192.168.1.12", "port": 8082 },
    "vendor":       { "host": "192.168.1.13", "port": 8083 },
    "ministry":     { "host": "192.168.1.14", "port": 8084 }
  }
  ```

* **Any** machine that has this project and a `sources.json` pointing at the
  same four IPs can run the mediator and get identical results — including a
  machine that is *also* running one of the four data sources. That is what
  satisfies "the professor runs the query on any one machine": every machine
  in the deployment carries the full codebase; which role a given machine
  plays is just which command you type on it.

---

## [A] Real deployment — 4 machines, 1 database each

You need 4 machines (laptops are fine) on the **same Wi-Fi / LAN**, each with
Python 3.9+ and this project copied onto it (git clone, USB stick, AirDrop,
shared folder — anything).

### Step 1 — install dependencies on every machine

```bash
pip install -r requirements.txt
```

### Step 2 — on each machine, build and run ONE service

| Machine | Command |
|---|---|
| 1 | `python data/build_databases.py --only manufacturer` then `python -m datasources.manufacturer_service` |
| 2 | `python data/build_databases.py --only distributor` then `python -m datasources.distributor_service` |
| 3 | `python data/build_databases.py --only vendor` then `python -m datasources.vendor_service` |
| 4 | `python data/build_databases.py --only ministry` then `python -m datasources.ministry_service` |

Each prints something like:

```
manufacturer data-source service
  local:  http://127.0.0.1:8081
  LAN:    http://192.168.1.11:8081   <-- put this in the other machines' sources.json
  health: http://192.168.1.11:8081/health
```

Write down the four **LAN** addresses it prints — that's what you'll need in
step 4. Leave all four processes running.

**If a machine is on a VPN**, the printed `LAN:` address can be wrong (it
picks up the VPN tunnel's address instead of the real Wi-Fi one - you'll see
a `[note]` about it when this looks likely). Double-check against
`ipconfig getifaddr en0` (macOS) / `ipconfig` (Windows) / `hostname -I`
(Linux) on that machine and use whichever address actually matches your
Wi-Fi adapter, or force it with `ASALI_LAN_IP=<correct ip>` before the
command.

### Step 3 — open the port on each machine's firewall

The service won't be reachable from other machines until this is done.

* **macOS**: System Settings → Network → Firewall → Options → allow incoming
  connections for `python3` (you'll usually just get a one-time popup the
  first time another machine connects — click **Allow**).
* **Windows**: Windows Defender Firewall → Advanced settings → Inbound Rules
  → New Rule → Port → TCP → the specific port (8081/8082/8083/8084) → Allow.
* **Linux**: `sudo ufw allow 8081/tcp` (swap the port per machine), or
  disable ufw for the LAN interface during the demo.

### Step 4 — on the machine that will run the GUI (any 5th machine, or reuse one of the 4)

```bash
cp sources.example.json sources.json
```

Edit `sources.json` and fill in the four LAN addresses from step 2. Then
sanity-check the network before touching the GUI:

```bash
python check_network.py
```

Expected output:

```
  OK    manufacturer http://192.168.1.11:8081    12.4 ms   23 rows across 3 tables
  OK    distributor  http://192.168.1.12:8082     9.1 ms   19 rows across 3 tables
  OK    vendor       http://192.168.1.13:8083    14.7 ms  157 rows across 3 tables
  OK    ministry     http://192.168.1.14:8084    11.0 ms   13 rows across 3 tables

All 4 data sources reachable. Safe to run the mediator.
```

If any line says `FAIL`, fix that machine (service not running? wrong IP in
`sources.json`? firewall blocking that port?) before continuing.

### Step 5 — start the GUI

```bash
python start.py --mediator-only
```

Open the URL it prints (`http://<this machine's IP>:8080/`) — from this
machine, or from any other machine on the same Wi-Fi, including the phone in
your pocket. Every `/item/<code>` lookup now genuinely fans out over the
network to 4 separate physical machines and joins the answers live.

### "The professor can run it on any one machine"

Because every machine has the same project + the same `sources.json`, the
professor can pick **any** of the 5 machines (including one already running a
data source), `cd` into the project there, and run
`python start.py --mediator-only` on top — it listens on a different port
(8080) than the data services (8081–8084), so there's no conflict, and it
will pull live data from the other 3 machines plus, over the network, its own
machine's service too.

---

## [B] 2-machine split — 2 databases per machine

If you can't get 4 laptops in one room, this is architecturally the same
thing with less setup, and it's covered end-to-end (firewall steps, IP
discovery, troubleshooting, proving it's real) in
**[DEPLOYMENT_TWO_MACHINE.md](DEPLOYMENT_TWO_MACHINE.md)**. Short version:

**Machine A** runs two of the four services:

```bash
python start.py --services=manufacturer,distributor
```

**Machine B** runs the other two:

```bash
python start.py --services=vendor,ministry
```

Each machine's `sources.json` only needs the *other* machine's two entries
(its own two default to `127.0.0.1`). On Machine A:

```json
{
  "vendor":   { "host": "192.168.1.12", "port": 8083 },
  "ministry": { "host": "192.168.1.12", "port": 8084 }
}
```

Then, from **whichever machine you want to demo from**, add
`--with-mediator` to its command (both can do this - they don't conflict,
since the mediator's port 8080 is independent of the data ports):

```bash
python check_network.py
python start.py --services=manufacturer,distributor --with-mediator   # on Machine A, e.g.
```

---

## Reverting to plain single-machine mode

Delete (or don't create) `sources.json` and run `python start.py` as before —
every service falls back to `127.0.0.1`, exactly like local development.
