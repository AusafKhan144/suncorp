#!/usr/bin/env python
"""Build the NRMA delivery CSVs + zip from the crawler's output.

    python make_delivery.py 202609            # every product
    python make_delivery.py 202609 Contents   # that product alone, own zip

Ships priced rows and genuine insurer declines only. Rows we failed to price
for our OWN reasons (Akamai 403, AkamaiNotValidated, rate limits) are dropped
rather than shipped — the client sees real insurer answers, never our block
messages. Samples never collected are simply absent, so row count is the
delivered sample count, not the input's.

"Genuine decline" is nrma_browser.TERMINAL_REASON_PREFIXES, the same list the
resume pass uses to decide a row needs no retry — one definition, not two.

Kept rows are copied as raw bytes, so CRLF and literal 'N/A' cells survive
untouched. Writes to outputs/<run>/delivery/ and leaves the crawler's own CSVs
alone, which matters because it rewrites them while it runs. The zip itself
lands in outputs/<run>/, where the Youi and RACV deliveries live.
"""
import csv
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nrma_browser import TERMINAL_REASON_PREFIXES  # noqa: E402

OUTPUTS = os.path.join(os.path.dirname(HERE), "outputs")


def shipworthy(row: dict) -> bool:
    """Priced, or declined by the insurer for a reason about the property."""
    priced = (row.get("NRMA_YearlyPayment") or "").strip()
    reason = (row.get("Reason_for_quote_failure") or "").strip()
    if reason.startswith(TERMINAL_REASON_PREFIXES):
        return True
    # A price with a failure reason is a quote that died part-way (2026-10-19:
    # a toggle whose excess sweep failed after its base prices were in). The
    # resume pass already retries it (_row_is_complete); it must not ship.
    if reason and reason != "N/A":
        return False
    try:
        return float(priced) > 0
    except ValueError:
        return False


def filter_csv(src: str, dst: str) -> tuple:
    """Copy shipworthy rows byte-for-byte. Returns (kept, dropped)."""
    with open(src, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8-sig")
    header, *lines = text.splitlines(keepends=True)
    fields = next(csv.reader(io.StringIO(header)))
    kept = []
    for line in lines:
        if not line.strip():
            continue
        row = dict(zip(fields, next(csv.reader(io.StringIO(line)))))
        if shipworthy(row):
            kept.append(line)
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        fh.write(header)
        fh.writelines(kept)
    return len(kept), len(lines) - len(kept)


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import period
    run = sys.argv[1] if len(sys.argv) > 1 else period.PERIOD
    # Optional product filter. A part-delivery gets its own zip name so it can
    # never be handed over as the full one -- same reason Youi's home-only
    # 202608 zip is named Youi_202608_Home_Data.zip.
    only = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else ""
    out = os.path.join(OUTPUTS, run)
    delivery = os.path.join(out, "delivery")
    os.makedirs(delivery, exist_ok=True)

    # Every CSV the crawlers wrote: a quarterly run ships Combined and both
    # toggles too (202608's zip had all five). Filtering on *_main.csv until
    # 2026-10-19 would have shipped 202611 without three of its five files.
    # Backups (*.csv.pre_*) do not end in .csv, so they never ship.
    names = sorted(f for f in os.listdir(out)
                   if f.endswith(".csv") and only.casefold() in f.casefold())
    if not names:
        print(f"[delivery] no {only or ''}*.csv in {out}")
        return 1
    for name in names:
        kept, dropped = filter_csv(os.path.join(out, name), os.path.join(delivery, name))
        print(f"[delivery] {name}: {kept} rows shipped, {dropped} blocked rows dropped")

    # Zip sits in outputs/<run>/ next to the Youi and RACV deliveries; only the
    # filtered CSVs live in delivery/, because they share the crawler's filenames.
    stem = f"NRMA_{run}_{only}_Data" if only else f"NRMA_{run}_Data"
    zip_path = os.path.join(out, f"{stem}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    subprocess.run(["zip", "-j", "-X", "-q", zip_path,
                    *[os.path.join(delivery, n) for n in names]], check=True)
    print(f"[delivery] {zip_path}")
    return 0


def demo() -> None:
    """Self-check: priced ships, insurer decline ships, our block does not."""
    assert shipworthy({"NRMA_YearlyPayment": "1222.45", "Reason_for_quote_failure": ""})
    assert shipworthy({"NRMA_YearlyPayment": "", "Reason_for_quote_failure":
                       "SumInsuredOutOfRange|contents|low|target=42000"})
    assert not shipworthy({"NRMA_YearlyPayment": "", "Reason_for_quote_failure":
                           "POST home/quote/QTEN0023292268 → 403: Akamai bot block"})
    assert not shipworthy({"NRMA_YearlyPayment": "", "Reason_for_quote_failure":
                           "AkamaiNotValidated: _abck never validated after 3 bootstrap attempts"})
    # a priced-as-zero row is not a quote, whatever the log said
    assert not shipworthy({"NRMA_YearlyPayment": "0", "Reason_for_quote_failure": ""})
    # a price with a failure reason died part-way and must not ship
    assert not shipworthy({"NRMA_YearlyPayment": "5050.15", "Reason_for_quote_failure":
                           "ExcessSweepIncomplete: no price at [3000] of 12 offered tiers"})
    print("demo ok")


if __name__ == "__main__":
    sys.exit(demo() if "--demo" in sys.argv else main())
