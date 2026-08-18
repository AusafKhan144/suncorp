#!/usr/bin/env python3
"""Post a collection-status snapshot to a Google Chat webhook.

Called every cron tick by run_motors.sh so you can monitor progress while away.
The webhook URL is read from (in order): env MONITOR_WEBHOOK_URL, then the file
NRMA/motors/webhook.conf (first non-comment line). If neither is set it logs and
exits quietly so cron never breaks.
"""
import os
import sys
import json
import subprocess
import datetime

import requests
import pandas as pd

sys.path.insert(0, "/home/wmc/WMC/DataManipulation/Suncorp")
import NRMA.motors.motors as m  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "webhook.conf")
DATA = "/home/wmc/WMC/DataManipulation/Suncorp/NRMA/inputs/202608/data"
OUT = "/home/wmc/WMC/DataManipulation/Suncorp/NRMA/outputs/202608"

# (label, input csv, output csv, toggle?)
JOBS = [
    ("Toggle", f"{DATA}/Motor_NRMA_quarterly_202608_toggle_newaddress.csv",
     f"{OUT}/Motor_NRMA_quarterly_202608_toggle.csv", True),
    ("Main", f"{DATA}/Motor_NRMA_quarterly_202608_main_newaddress.csv",
     f"{OUT}/Motor_NRMA_quarterly_202608_main.csv", False),
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


def job_stats(inp: str, outp: str, toggle: bool) -> dict:
    quotable = set()
    if os.path.exists(inp):
        df = pd.read_csv(inp, dtype=str)
        rows = df.where(pd.notna(df), None).to_dict("records")
        quotable = {
            str(r.get("Sample_Number", "")).strip()
            for r in rows if m._should_quote_row(r)
        }
        quotable.discard("")

    done = failed = 0
    if os.path.exists(outp):
        odf = pd.read_csv(outp, dtype=str)
        seen = set()
        for _, r in odf.iterrows():
            sid = str(r.get("Sample_Number", "")).strip()
            if not sid or sid in seen or sid not in quotable:
                continue
            seen.add(sid)
            reason = str(r.get("Reason_for_quote_failure", "")).strip()
            if reason and reason.lower() not in ("", "nan", "none"):
                if not m._is_retryable(reason):
                    failed += 1            # terminal failure
            elif m._has_required_prices(r, toggle):
                done += 1                  # genuine success
    total = len(quotable)
    pending = max(total - done - failed, 0)
    pct = (100 * done / total) if total else 0
    return {"total": total, "done": done, "failed": failed,
            "pending": pending, "pct": pct}


def scraper_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", "[m]otors.py"], capture_output=True
        ).returncode == 0
    except Exception:
        return False


def build_message() -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    state = "🟢 RUNNING" if scraper_running() else "⚪ idle"
    lines = [f"*NRMA Motors* — {now}  ({state})"]
    for label, inp, outp, toggle in JOBS:
        s = job_stats(inp, outp, toggle)
        lines.append(
            f"• {label}: {s['done']}/{s['total']} done ({s['pct']:.1f}%) "
            f"| pending {s['pending']} | failed {s['failed']}"
        )
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
