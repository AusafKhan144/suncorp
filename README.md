# Suncorp competitor pricing — NRMA · Youi · RACV

Monthly collection of home and motor quotes. One period per run, one input CSV
per insurer/product, one output CSV mirroring its name.

## Running a new period

**1. Drop the input CSVs.** One folder per insurer, named for the period:

```
<Insurer>/inputs/<YYYYMM>/data/<Product>_<TAG>_<cadence>_<YYYYMM>_main.csv
```

`TAG` is `NRMA`, `YOUI` or `RACV`. `Product` is `Buildings`, `Contents` or
`Motor`. The cadence word (`monthly` / `quarterly`) is yours to choose — it is
read back out of the filename, never reconstructed, so the two can differ
between periods without a code change. Drop the matching
`delivery_template/` and `lookup/` files beside `data/` as usual.

**2. Check what will run.** Nothing else needs editing:

```bash
python period.py
```

It prints the resolved period, every product it found, input and output row
counts, and Youi's chunk count. An insurer you have not dropped yet simply does
not appear. If it names the wrong period, either export `SUNCORP_PERIOD=YYYYMM`
or write that value into a `PERIOD` file at the repo root — otherwise the newest
`inputs/<YYYYMM>/data` wins.

NRMA writes the client's `delivery_template` columns (Suncorp, 2026-10-22),
plus one `Rerun_` column per field the drop's `lookup` marks "Bot allowed to
toggle to achieve desired SI", after the template's three (Suncorp,
2026-10-29). `python NRMA/home/test_template_columns.py` fails if this drop's
template or lookup changed; update the scrapers' `MAIN_FIELDS`/`TOGGLE_FIELDS`
or `si_steps.py`'s field lists before collecting.

**3. Split Youi into chunks** (Youi only — it fans out across 10 runners per
file). A quarterly drop's two toggles and Combined are picked up as well, and
the samples all three share go in chunk 1 of each so they are quoted together:

```bash
python chunkify.py
```

**4. Start collecting.**

```bash
cd NRMA/home && setsid nohup python3 launch_free_pool.py contents.py >> contents_stdout.log 2>&1 &
cd NRMA/home && setsid nohup python3 launch_free_pool.py building.py >> buildings_stdout.log 2>&1 &
cd NRMA/home && setsid nohup python3 launch_free_pool.py combined.py >> combined_stdout.log 2>&1 &   # quarterly only
python run_youi.py all          # `python run_youi.py list` to see the configs first
```

`launch_free_pool.py` picks the first proxy pool that is not resting — never
`direct` (the office IP): unattended and unpaced it gets bot-flagged, which cost
46h of zero rows on 2026-10-07. Use direct only by hand, paced and alone
(`NRMA_PROXY_PROVIDER=direct NRMA_PROFILE_DELAY=90 python3 contents.py`). The
`mobile` pool (AU carrier IPs) is tried first once `NRMA/home/secrets.conf` has
one `MOBILE_PROXY_URL=http://USER:PASS@host:port` line, with `{session}` where
the provider's sticky-session id goes; without that line it does not exist. A pool
name may carry a country (`iproyal-gb` = iProyal's UK IPs, own cooldown): NRMA
neither geo-prices nor geo-blocks, so the launcher rotates UK → US → AU on the same
plans. To add a country, add one entry to `PROXY_POOLS` in `nrma_browser.py`. Both NRMA
scrapers resume from their own output CSV, so re-running one is always safe and
never re-collects a row that already has a price. Youi's runners start 30s
apart per chunk slot, so a quarterly run (5 files × 10 runners) ramps up over
~4.5 min rather than opening 50 browsers in the same second.

**5. Turn the status card back on.** One line in `crontab -e`, already written
and commented out at the bottom — it posts progress every 2 hours *and*
relaunches a scraper (NRMA, or the whole Youi fan-out) that died with work
pending. A Youi relaunch that lands nothing is held 24h (`⏸` on the card says
when the next try is due), and files that were dropped but are not collected
here (stored for safekeeping) get one `stored only` line instead of red alerts:

```
0 */2 * * * /home/wmc/anaconda3/bin/python3 <repo>/monitor_status.py >> <repo>/monitor.log 2>&1
```

The card is period-agnostic now. Check it any time without posting or
relaunching anything:

```bash
MONITOR_WEBHOOK_URL=- MONITOR_AUTOSTART=0 python monitor_status.py
```

**6. Ship.** Priced rows plus genuine insurer declines only; our own blocks are
dropped, never shipped with their reason text:

```bash
python NRMA/home/make_delivery.py           # all products, this period
python NRMA/home/make_delivery.py 202609 Contents   # one product, its own zip
```

The delivered row count is the *delivered sample count*. Tell the client that
number — a pre-sized template sheet will otherwise mis-align silently.

## Where the period comes from

`period.py` is the only place that knows. Every entry point reads it:
`NRMA/home/{building,contents}.py`, `run_youi.py`, `Youi/home/runner.py`,
`chunkify.py`, `monitor_status.py`, `NRMA/home/make_delivery.py`. Env vars
(`INPUT_MAIN_CSV`, `OUTPUT_MAIN_CSV`, …) still override per run, which is how a
single-sample rerun is driven without editing anything.

A toggle input only ships with a quarterly drop. When it is absent the NRMA
scrapers skip that stage rather than falling back to another period's file.

## Checks

```bash
python period.py --selftest              # period + filename resolution
python monitor_status.py --selftest      # card classifier + relaunch hold
python Youi/home/test_address_retry.py   # Youi address candidate walk
python Youi/home/test_toggle_schema.py   # toggle CSV header is first-row-proof
python NRMA/home/test_prefill_storeys.py # fractional storey_count guard
python NRMA/home/test_block_breaker.py   # 403 backoff + circuit breaker
python NRMA/home/test_template_columns.py # outputs = delivery_template + lookup Yes fields
python NRMA/home/si_steps.py             # sum-insured re-quotes (Suncorp's strip, then large bedrooms)
python NRMA/home/make_delivery.py --demo # delivery filter
```

## Layout

| Path | What |
|---|---|
| `period.py` | resolves the period and every input/output path |
| `NRMA/home/` | NRMA scrapers: `contents.py`, `building.py`, shared `nrma_browser.py` |
| `NRMA/home/launch_free_pool.py` | starts a scraper on the first non-resting proxy pool |
| `NRMA/home/probe_ui_journey.py` | "is it us or the network?" in one browser launch — run this first when NRMA starts refusing |
| `NRMA/home/reference/` | captured real-browser journeys; the `.har` solved the 403 wall |
| `Youi/home/`, `Youi/motors/` | Youi crawlers, driven by `run_youi.py` / `run_motors_v2.py` |
| `RACV/` | inputs and outputs only — RACV is collected outside this repo |
| `monitor_status.py` | status card + relaunch of a dead NRMA scraper |

Decisions, open items and the reasoning behind the proxy and blocking work live
in the Obsidian vault under `Projects/Suncorp/`.
