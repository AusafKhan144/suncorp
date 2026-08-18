#!/usr/bin/env python3
"""Post an NRMA home collection-status snapshot to a Google Chat webhook.

Run every 30 min from cron. Counts the output CSVs (which are the scrapers' only
checkpoint), reports per-product progress, and relaunches a scraper that has died
with work still pending.

The webhook URL is read from (in order): env MONITOR_WEBHOOK_URL, NRMA/home/
webhook.conf, NRMA/motors/webhook.conf (first line starting with http). If none
is set it prints the snapshot instead, so cron never breaks.

  python monitor_status.py               # report + restart dead runs
  MONITOR_WEBHOOK_URL=- python monitor_status.py   # print, don't post
  MONITOR_AUTOSTART=0 python monitor_status.py     # never relaunch
  python monitor_status.py --selftest    # classifier asserts
"""
import csv
import datetime
import json
import os
import subprocess
import sys

import requests

# Only the terminal-reason prefixes are shared, not the whole scraper:
# building.py runs logging.basicConfig(FileHandler("scraper.log")) at import and
# pulls httpx, while nrma_browser is stdlib-only at module level (camoufox is
# imported lazily inside the session helper). A broken scraper dependency must
# not take down the monitor — that is exactly when the report matters.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nrma_browser import TERMINAL_REASON_PREFIXES  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/wmc/WMC/DataManipulation/Suncorp/NRMA"
STATE = os.path.join(HERE, "monitor_state.json")
CONFS = [os.path.join(HERE, "webhook.conf"),
         os.path.join(ROOT, "motors", "webhook.conf")]
# The interpreter the live runs use (/proc/<pid>/exe).
PY = os.getenv("PYTHON", "/home/wmc/anaconda3/bin/python")
AUTOSTART = os.getenv("MONITOR_AUTOSTART", "1").strip().lower() not in (
    "0", "false", "no", "n",
)
STALL_MIN = 60   # no new row in this long, while alive, means wedged not slow

# (label, input csv, output csv, script, pgrep pattern)
# Paths mirror the scrapers' env-var defaults. Inputs say "monthly", outputs say
# "quarterly" — that is how this dataset is named; never derive one from the other.
JOBS = [
    ("Buildings",
     f"{ROOT}/inputs/20260811/data/Buildings_NRMA_monthly_202608_main.csv",
     f"{ROOT}/outputs/20260811/Buildings_NRMA_quarterly_202608_main.csv",
     "building.py", "[b]uilding.py"),
    ("Contents",
     f"{ROOT}/inputs/20260811/data/Contents_NRMA_monthly_202608_main.csv",
     f"{ROOT}/outputs/20260811/Contents_NRMA_quarterly_202608_main.csv",
     "contents.py", "[c]ontents.py"),
]


def webhook_url() -> str:
    url = os.getenv("MONITOR_WEBHOOK_URL", "").strip()
    if url:
        # Set but not a URL (MONITOR_WEBHOOK_URL=-) means "don't post" — the conf
        # fallback must not override an explicit opt-out.
        return url if url.startswith("http") else ""
    for conf in CONFS:
        if not os.path.exists(conf):
            continue
        with open(conf, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("http"):   # skips comments + placeholder
                    return line
    return ""


def classify(row: dict) -> str:
    """Bucket one output row the way the scraper's _row_is_complete() would.

    ok + terminal == "collected" (a resume pass skips them); retrying is a
    transient failure the next pass re-runs.
    """
    reason = (row.get("Reason_for_quote_failure") or "").strip()
    if reason:
        return "terminal" if reason.startswith(TERMINAL_REASON_PREFIXES) else "retrying"
    return "ok" if (row.get("NRMA_YearlyPayment") or "").strip() else "retrying"


def job_stats(inp: str, outp: str) -> dict:
    samples = set()
    if os.path.exists(inp):
        with open(inp, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                samples.add((row.get("Sample_Number") or "").strip())
        samples.discard("")

    counts = {"ok": 0, "terminal": 0, "retrying": 0}
    age = None
    if os.path.exists(outp):
        seen = set()
        with open(outp, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                sid = (row.get("Sample_Number") or "").strip()
                if not sid or sid in seen or (samples and sid not in samples):
                    continue
                seen.add(sid)
                counts[classify(row)] += 1
        # mtime is the last row written: catches a wedged-but-alive process,
        # which the row count alone cannot tell apart from a dead one.
        age = (datetime.datetime.now().timestamp() - os.path.getmtime(outp)) / 60

    total = len(samples)
    done = counts["ok"] + counts["terminal"]
    return {
        "total": total, "done": done, **counts,
        "pending": max(total - done - counts["retrying"], 0),
        "pct": (100 * done / total) if total else 0,
        "age_min": age,
    }


def running(pattern: str) -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True
        ).returncode == 0
    except Exception:
        return False


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def restart(script: str) -> bool:
    """Relaunch a scraper, detached so it outlives this cron tick."""
    try:
        subprocess.Popen(
            [PY, script], cwd=HERE, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(f"[monitor] restart {script} failed: {e}")
        return False


def bar(pct: float, width: int = 22) -> str:
    filled = round(pct * width / 100)
    return "▓" * filled + "░" * (width - filled)


def _job_block(label: str, s: dict, was: dict, now, remaining: int) -> list[str]:
    """Three monospace lines for one product. Emoji stay out — they break the
    column alignment Google Chat gives inside a ``` block."""
    block = [
        f"{label.upper():<10}{s['pct']:>5.1f}%  {bar(s['pct'])}  "
        f"{s['done']}/{s['total']}",
        f"{'':<10}ok {s['ok']} · terminal {s['terminal']} · "
        f"retrying {s['retrying']} · todo {s['pending']}",
    ]
    rate = []
    if was:
        delta = s["done"] - was["done"]
        mins = (now.timestamp() - was["ts"]) / 60
        if mins >= 1:
            rate.append(f"+{delta} in {mins:.0f}m")
    # ETA off one 30-min tick swings by 10x on a job this slow (one row can take
    # 2 min), so measure against the oldest observation instead.
    anchor = (was or {}).get("anchor")
    if anchor and remaining:
        gained = s["done"] - anchor["done"]
        span = (now.timestamp() - anchor["ts"]) / 60
        if gained > 0 and span >= 20:
            rate.append(f"eta {remaining * span / gained / 60:.1f}h "
                        f"(at {gained / span * 60:.0f}/h)")
    if s["age_min"] is not None:
        rate.append(f"last row {s['age_min']:.0f}m ago")
    if rate:
        block.append(f"{'':<10}" + " · ".join(rate))
    return block


def build_message() -> str:
    now = datetime.datetime.now()
    prev, state = load_state(), {}
    heads, rows, alerts = [], [], []
    grand_done = grand_total = 0

    for label, inp, outp, script, pattern in JOBS:
        s = job_stats(inp, outp)
        alive = running(pattern)
        grand_done += s["done"]
        grand_total += s["total"]
        # Retryable rows are real remaining work, so they justify a relaunch.
        # ponytail: a row that keeps failing non-terminally relaunches the scraper
        # every 30 min forever — cap it with an attempt count in monitor_state.json
        # if that shows up in cron.log.
        remaining = s["pending"] + s["retrying"]

        if not alive and remaining and AUTOSTART and restart(script):
            alerts.append(f"♻ *{label}* was dead — relaunched `{script}`")
            alive = True
        elif not alive and remaining:
            alerts.append(f"🔴 *{label}* not running, {remaining} rows outstanding")
        elif alive and (s["age_min"] or 0) > STALL_MIN:
            # Alive but no new row in an hour: wedged, not slow. A row takes
            # 60-120s even with jitter and ban retries.
            alerts.append(
                f"⚠️ *{label}* alive but no new row in {s['age_min']:.0f}m"
            )
        elif not remaining:
            alerts.append(f"✅ *{label}* complete")

        heads.append(f"{'🟢' if alive else '⚪'} {label.lower()}")
        rows += _job_block(label, s, prev.get(label), now, remaining)
        # Carry the oldest observation forward as the ETA baseline. A restart
        # truncates incomplete rows from the CSV, so done can fall — re-anchor
        # rather than report a negative rate.
        anchor = (prev.get(label) or {}).get("anchor")
        if not anchor or s["done"] < anchor["done"]:
            anchor = {"done": s["done"], "ts": now.timestamp()}
        state[label] = {"done": s["done"], "ts": now.timestamp(),
                        "clock": now.strftime("%H:%M"), "anchor": anchor}

    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception as e:
        print(f"[monitor] state write failed: {e}")

    overall = (100 * grand_done / grand_total) if grand_total else 0
    rows.append(f"{'TOTAL':<10}{overall:>5.1f}%  {bar(overall)}  "
                f"{grand_done}/{grand_total}")
    return "\n".join([
        f"*NRMA Home* — {now:%a %d %b %H:%M}   {' · '.join(heads)}",
        "```", *rows, "```", *alerts,
    ])


def _selftest():
    cases = [
        ({"Reason_for_quote_failure": "", "NRMA_YearlyPayment": "3655.96"}, "ok"),
        ({"Reason_for_quote_failure": "SumInsuredOutOfRange|contents|low|target=76000",
          "NRMA_YearlyPayment": ""}, "terminal"),
        ({"Reason_for_quote_failure": "No address found: 32 FAKE ST",
          "NRMA_YearlyPayment": ""}, "terminal"),
        ({"Reason_for_quote_failure": "We don't cover properties in your area",
          "NRMA_YearlyPayment": ""}, "terminal"),
        ({"Reason_for_quote_failure": "POST quote/QTEN00225 → 403: Akamai bot block",
          "NRMA_YearlyPayment": ""}, "retrying"),
        # written but priceless: incomplete, the next pass re-runs it
        ({"Reason_for_quote_failure": "", "NRMA_YearlyPayment": ""}, "retrying"),
        ({}, "retrying"),
    ]
    for row, want in cases:
        got = classify(row)
        assert got == want, f"{row} → {got}, want {want}"

    # ok + terminal must equal the scraper's own _row_is_complete verdict.
    def row_is_complete(row):
        reason = (row.get("Reason_for_quote_failure") or "").strip()
        if reason:
            return reason.startswith(TERMINAL_REASON_PREFIXES)
        return bool((row.get("NRMA_YearlyPayment") or "").strip())

    for row, _ in cases:
        assert (classify(row) in ("ok", "terminal")) == row_is_complete(row), row

    # 60 rows in 120 min = 30/h, so 100 remaining is 3.3h away.
    now = datetime.datetime.now()
    s = {"pct": 6.0, "done": 60, "total": 1000, "ok": 60, "terminal": 0,
         "retrying": 0, "pending": 100, "age_min": 2.0}
    was = {"done": 57, "ts": now.timestamp() - 1800,
           "anchor": {"done": 0, "ts": now.timestamp() - 7200}}
    line = _job_block("Test", s, was, now, 100)[2]
    assert "+3 in 30m" in line and "eta 3.3h" in line and "at 30/h" in line, line
    # too short a span to trust: delta shown, ETA withheld
    short = {"done": 57, "ts": now.timestamp() - 300,
             "anchor": {"done": 57, "ts": now.timestamp() - 300}}
    assert "eta" not in _job_block("Test", s, short, now, 100)[2]
    print(f"[monitor] selftest ok ({len(cases)} rows + rate math)")


def main():
    if "--selftest" in sys.argv:
        _selftest()
        return
    url = webhook_url()
    msg = build_message()
    if not url:
        print("[monitor] no webhook URL configured — status:\n" + msg)
        return
    try:
        resp = requests.post(
            url, json={"text": msg},
            headers={"Content-Type": "application/json"}, timeout=20,
        )
        print(f"[monitor] sent ({resp.status_code})")
    except Exception as e:
        print(f"[monitor] webhook post failed: {e}\n{msg}")


if __name__ == "__main__":
    main()
