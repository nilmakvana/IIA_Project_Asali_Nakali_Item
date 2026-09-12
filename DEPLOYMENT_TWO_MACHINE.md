# Two-machine deployment — 2 databases per machine, either one can be the GUI

For when you have exactly **2 machines** (2 laptops, or a laptop + a desktop)
on the same Wi-Fi / LAN, want the 4 databases genuinely **split 2-and-2**
across them, and want **either machine** to be able to run the mediator/GUI
on demand — not one fixed "GUI machine".

This is a self-contained guide — you don't need `DEPLOYMENT.md` (the
4-machine, 1-database-each version) to follow this one.

---

## The architecture

```
┌───────────────────────────────┐   Wi-Fi / LAN   ┌───────────────────────────────┐
│   MACHINE A                    │ ◄──────────────► │   MACHINE B                    │
│                                 │   HTTP / JSON     │                                 │
│  manufacturer  :8081  ┐         │                   │  vendor        :8083  ┐         │
│  distributor   :8082  ┴ 2 DBs   │                   │  ministry      :8084  ┴ 2 DBs   │
│                                 │                   │                                 │
│  manufacturer.db                │                   │  vendor.db                      │
│  distributor.db                 │                   │  ministry.db                    │
│                                 │                   │                                 │
│  mediator + GUI  :8080  (optional, on demand)        │  mediator + GUI  :8080  (optional, on demand)
└───────────────────────────────┘                   └───────────────────────────────┘
```

* **Machine A** hosts the `manufacturer` and `distributor` databases/APIs.
* **Machine B** hosts the `vendor` and `ministry` databases/APIs.
* **Either machine can additionally run the mediator/GUI** on port 8080 at
  any time — it's just one extra flag on the command you already run. The
  mediator always answers a query using **all 4** sources: 2 it can reach
  locally (`127.0.0.1`) and 2 it reaches over the network on the other
  machine — it makes no distinction between the two, so the result is
  identical no matter which machine you ask from.
* Neither machine's mediator ever needs the *other* machine's database
  files — only its two REST APIs.

This is the real thing, not a simplification: kill a service on either
machine and the corresponding data vanishes from whichever machine's GUI
you're looking at, immediately.

---

## Prerequisites

* Both machines on the **same Wi-Fi network / LAN** (not a guest network with
  client/AP isolation switched on — see Troubleshooting).
* Python 3.9+ on both machines.
* This project copied onto **both** machines, identically (git clone, USB
  stick, AirDrop, a shared folder — anything).

```bash
pip install -r requirements.txt        # on BOTH machines
```

---

## Step 1 — find both machines' LAN IPs

* **macOS**: `ipconfig getifaddr en0` (or `en1` for older Macs on Wi-Fi)
* **Windows**: `ipconfig` → "IPv4 Address" under the active adapter
* **Linux**: `hostname -I`

Write both down. In this guide: **Machine A = `192.168.1.11`**,
**Machine B = `192.168.1.12`** — replace with your real addresses everywhere
below.

---

## Step 2 — each machine's `sources.json`

Each machine only needs an entry for the **other** machine's two services —
its own two are reached at `127.0.0.1` automatically (that's `config.py`'s
default when a source isn't listed in `sources.json`), so you don't even
need to write them down.

**On Machine A**, create `sources.json`:

```json
{
  "vendor":   { "host": "192.168.1.12", "port": 8083 },
  "ministry": { "host": "192.168.1.12", "port": 8084 }
}
```

**On Machine B**, create `sources.json`:

```json
{
  "manufacturer": { "host": "192.168.1.11", "port": 8081 },
  "distributor":  { "host": "192.168.1.11", "port": 8082 }
}
```

(`sources.example.json` in the project root shows the full 4-entry form if
you'd rather list all four explicitly on both machines — either form works,
since a missing entry just falls back to `127.0.0.1`.)

---

## Step 3 — start each machine's two services

**On Machine A:**

```bash
python start.py --services=manufacturer,distributor
```

**On Machine B:**

```bash
python start.py --services=vendor,ministry
```

Each builds its own two databases on first run and starts those two APIs
bound to the network (not just `localhost`). You'll see:

```
Starting ...
  ready: manufacturer
  ready: distributor

  manufacturer  http://192.168.1.11:8081   (this machine)
  distributor   http://192.168.1.11:8082   (this machine)

Running. Press Ctrl+C to stop.
```

Leave both running.

---

## Step 4 — open the firewall for each machine's two ports

* **macOS**: the first time the other machine connects, macOS should prompt
  *"Do you want the application 'python3' to accept incoming network
  connections?"* → **Allow**. If it doesn't ask: System Settings → Network →
  Firewall → Options → add `python3` and allow it.
* **Windows**: Windows Defender Firewall → Advanced settings → Inbound Rules
  → New Rule → Port → TCP → Specific local ports → `8081,8082` (on Machine A)
  or `8083,8084` (on Machine B) → Allow → apply to your Wi-Fi's profile.
* **Linux**: `sudo ufw allow 8081:8082/tcp` (Machine A) /
  `sudo ufw allow 8083:8084/tcp` (Machine B).

---

## Step 5 — sanity-check the network before opening the GUI

**On Machine A:**

```bash
python check_network.py
```

Expected — note only Machine A's OWN two show up as `(local)`-fast, the
other two round-trip over Wi-Fi:

```
  OK    manufacturer http://127.0.0.1:8081        5.9 ms   23 rows across 3 tables
  OK    distributor  http://127.0.0.1:8082        1.7 ms   19 rows across 3 tables
  OK    vendor       http://192.168.1.12:8083    10.2 ms  157 rows across 3 tables
  OK    ministry     http://192.168.1.12:8084     5.8 ms   12 rows across 3 tables

All 4 data sources reachable. Safe to run the mediator (python start.py --mediator-only).
```

(Machine B's `sources.json` gives the mirror image — its own two fast and
local, Machine A's two over the network.) Run `python check_network.py` on
**Machine B too** — both should say all 4 `OK` before you demo anything.

If either line says `FAIL`, see Troubleshooting.

---

## Step 6 — run the GUI from *either* machine

This is the point of the whole setup: you don't pick a "GUI machine" in
advance. Whichever machine you want to demo from, add `--with-mediator` to
its already-running command (stop it with `Ctrl+C` and restart with the flag,
or just start it this way from the beginning):

**To demo from Machine A:**

```bash
python start.py --services=manufacturer,distributor --with-mediator
```

**To demo from Machine B instead** (Machine A keeps running its own two
services in the background, unchanged):

```bash
python start.py --services=vendor,ministry --with-mediator
```

Either way, open the URL it prints for the GUI —
`http://<that machine's IP>:8080/` — and every lookup fetches all 4 sources:
two answered locally, two fetched live from the other machine.

You can even run the mediator on **both machines at the same time** (they're
on different physical machines so port 8080 doesn't clash) — whoever's
sitting at either laptop gets the same answers.

---

## Proving it's real, not cached

* Stop the `vendor`/`ministry` process on Machine B. Reload an item on
  Machine A's GUI — vendor- and ministry-sourced fields (scans, sales,
  counterfeit reports, registry status) disappear or show as unreachable in
  the "Query decomposition" panel, while manufacturer/distributor data (which
  Machine A serves itself) is unaffected.
* Open `http://<Machine B's IP>:8083/docs` directly from Machine A's browser
  — that's FastAPI's live Swagger UI for the vendor API, served only by
  Machine B, with no mediator involved.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `check_network.py` `FAIL` — connection refused | The other machine's service isn't running, or the port is wrong | Check the other machine's terminal still shows "Running"; re-check `sources.json` |
| `check_network.py` `FAIL` — timeout, nothing responds | Firewall blocking the port, or Wi-Fi **client/AP isolation** (common on phone hotspots, guest/campus Wi-Fi) | Redo Step 4; if it's AP isolation, switch both machines to a network without it (a personal router, or a hotspot with isolation disabled) |
| A machine's IP changed | DHCP reassigned it (e.g. after sleep) | Re-run the Step 1 command on that machine, update the *other* machine's `sources.json`, re-run `check_network.py` |
| GUI shows only 2 sources' worth of data even with both machines running | `sources.json` on the machine you're running the mediator from is missing or wrong | Re-check that machine's `sources.json` has the *other* machine's two services listed with the right IP |
| Port already in use | A previous run is still holding it | `Ctrl+C` the old process (or find and kill it) before restarting |
| The `LAN:` address `start.py` prints doesn't match what `ipconfig getifaddr en0` / `ipconfig` / `hostname -I` shows for that machine | You're on a VPN (or another active network adapter) that's grabbed the machine's default route, so the auto-detect trick returns the VPN tunnel's address (often `10.x.x.x`) instead of your real Wi-Fi IP - the tool prints a `[note]` when this looks likely | Use the IP from `ipconfig`/`ifconfig` (not the printed one) in the *other* machine's `sources.json`; or set `ASALI_LAN_IP=<correct ip>` before the command so the banner itself is right too. If it's still unreachable, the VPN may be blocking LAN traffic entirely - disconnect it for the demo. |

---

## Reverting to single-machine mode

Delete `sources.json` and run `python start.py` (no flags) on one machine —
all four services plus the mediator start locally, exactly like development.

---

## Going further

* **4 machines, 1 database each**: see [DEPLOYMENT.md](DEPLOYMENT.md) — same
  `sources.json` mechanism, just one `--services=<name>` per machine instead
  of two.
* **A different 2-and-2 split** (e.g. manufacturer+vendor on one machine,
  distributor+ministry on the other): works exactly the same way — just
  change which two names you pass to `--services=` on each machine and which
  two entries go in each `sources.json`. The split doesn't have to follow any
  particular grouping.
