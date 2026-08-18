#!/usr/bin/env python3
"""Post a Youi (home) collection-status snapshot to a Google Chat webhook.

Called every cron tick so you can monitor progress while away. Mirrors
NRMA/motors/monitor_status.py. The webhook URL is read from (in order):
env MONITOR_WEBHOOK_URL, then Youi/home/webhook.conf (first non-comment line).
If neither is set it logs the status and exits quietly so cron never breaks.

Progress is derived from the aggregated output CSVs in OUT against the total
input rows spread across the per-chunk input files in DATA.
"""
import os
import sys
import glob
import json
import subprocess
import datetime

import requests
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "webhook.conf")
PERIOD = "202608"
ROOT = "/home/wmc/WMC/DataManipulation/Suncorp/Youi"
DATA = f"{ROOT}/inputs/{PERIOD}/data/chunks"
OUT = f"{ROOT}/outputs/{PERIOD}"

# (label, filename stem — shared by the input chunks and the output csv)
JOBS = [
    ("Buildings main", "Buildings_YOUI_quarterly_202608_main"),
    ("Buildings toggle", "Buildings_YOUI_quarterly_202608_toggle"),
    ("Contents main", "Contents_YOUI_quarterly_202608_main"),
    ("Contents toggle", "Contents_YOUI_quarterly_202608_toggle"),
    ("Combined", "Combined_YOUI_quarterly_202608"),
]


def webhook_url() -> str:
    url = os.getenv("MONITOR_WEBHOOK_URL", "").strip()
    if url.startswith("http"):
        return url
    if os.path.exists(CONF):
        with open(CONF, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("http"):   # ignore comments + placeholder
                    return line
    return ""


def _blank(val) -> bool:
    s = str(val).strip().lower()
    return s in ("", "nan", "none", "n/a", "na")


def job_stats(filename: str) -> dict:
    # total = unique sample numbers across every input chunk for this stem
    total_ids = set()
    for chunk in glob.glob(os.path.join(DATA, f"{filename}_chunk_*.csv")):
        try:
            df = pd.read_csv(chunk, usecols=["SAMPLE_NUMBER"], dtype=str)
        except Exception:
            continue
        total_ids.update(str(s).strip() for s in df["SAMPLE_NUMBER"].dropna())
    total_ids.discard("")
    total = len(total_ids)

    done = failed = 0
    outp = os.path.join(OUT, f"{filename}.csv")
    if os.path.exists(outp):
        try:
            odf = pd.read_csv(outp, dtype=str)
        except Exception:
            odf = pd.DataFrame()
        seen = set()
        for _, r in odf.iterrows():
            sid = str(r.get("sample_number", "")).strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            if not _blank(r.get("Reason_for_quote_failure", "")):
                failed += 1                       # terminal quote failure
            elif not _blank(r.get("QuoteNumber", "")):
                done += 1                          # genuine success
            else:
                failed += 1                        # processed, no quote
    pending = max(total - done - failed, 0)
    pct = (100 * done / total) if total else 0
    return {"total": total, "done": done, "failed": failed,
            "pending": pending, "pct": pct}


def scraper_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", "[r]unner.py"], capture_output=True
        ).returncode == 0
    except Exception:
        return False


def build_message() -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    state = "🟢 RUNNING" if scraper_running() else "⚪ idle"
    lines = [f"*Youi Home* — {now}  ({state})"]
    grand_done = grand_total = 0
    for label, filename in JOBS:
        s = job_stats(filename)
        grand_done += s["done"]
        grand_total += s["total"]
        lines.append(
            f"• {label}: {s['done']}/{s['total']} done ({s['pct']:.1f}%) "
            f"| pending {s['pending']} | failed {s['failed']}"
        )
    overall = (100 * grand_done / grand_total) if grand_total else 0
    lines.append(f"*Overall*: {grand_done}/{grand_total} ({overall:.1f}%)")
    return "\n".join(lines)


def main():
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
        print(f"[monitor] webhook post failed: {e}")


if __name__ == "__main__":
    main()
