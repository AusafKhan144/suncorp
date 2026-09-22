#!/usr/bin/env python3
"""Where this run's CSVs live. One place, so a new period is a data drop.

    python period.py              # show what a launch would collect, and from where
    python period.py --selftest   # resolution asserts

**To start a new period: drop the input CSVs into `<Insurer>/inputs/<YYYYMM>/data/`
and run `python period.py` to check.** Nothing else needs editing — every entry
point (both NRMA scrapers, run_youi.py, chunkify.py, Youi/home/runner.py,
monitor_status.py, make_delivery.py) resolves its paths through here.

Period resolution, first hit wins:
  1. `$SUNCORP_PERIOD`               — one run, no edits
  2. the `PERIOD` file beside this   — pin it, e.g. to re-open a closed month
  3. the newest `inputs/<YYYYMM>/data` any insurer has — the zero-config default

**Filenames are read, never built.** The cadence word in a name (`monthly` vs
`quarterly`) is part of the drop and has been wrong on the output side before —
2026-09 inputs say "monthly" while the same period's earlier outputs said
"quarterly". So the input CSV is found by *shape* (`Contents_NRMA_*.csv`) and
the output simply mirrors whatever basename the drop actually used. That also
means a period can change cadence without touching a line of code.

An absent file is None, not an exception: the monitor must still render a card
when an insurer has not been dropped yet, and a scraper reports one clear line
instead of a traceback.
"""
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Directory name -> the tag that insurer uses inside CSV filenames.
INSURERS = {"NRMA": "NRMA", "Youi": "YOUI", "RACV": "RACV"}
PRODUCTS = ("Buildings", "Contents", "Motor")

_PERIOD_RE = re.compile(r"^\d{6}$")


def periods() -> list[str]:
    """Every YYYYMM any insurer has dropped input data for, oldest first.

    Only `<period>/data` counts. A bare `inputs/<period>/` with just a
    delivery_template is a half-prepared drop, not a runnable period.
    """
    found = set()
    for insurer in INSURERS:
        for data in glob.glob(os.path.join(HERE, insurer, "inputs", "*", "data")):
            name = os.path.basename(os.path.dirname(data))
            if _PERIOD_RE.match(name):
                found.add(name)
    return sorted(found)


def resolve_period() -> tuple[str, str]:
    """(period, where it came from). Empty period when nothing is dropped yet."""
    env = os.getenv("SUNCORP_PERIOD", "").strip()
    if env:
        return env, "$SUNCORP_PERIOD"
    pin = os.path.join(HERE, "PERIOD")
    if os.path.exists(pin):
        with open(pin) as fh:
            value = fh.read().strip()
        if value:
            return value, "PERIOD file"
    available = periods()
    if available:
        return available[-1], "newest inputs dir"
    return "", "nothing dropped"


PERIOD, PERIOD_SOURCE = resolve_period()


def data_dir(insurer: str) -> str:
    return os.path.join(HERE, insurer, "inputs", PERIOD, "data")


def out_dir(insurer: str) -> str:
    return os.path.join(HERE, insurer, "outputs", PERIOD)


def input_csv(insurer: str, product: str, kind: str = "main") -> str | None:
    """This period's input CSV for one product, or None if it was not dropped.

    `kind` is "main" or "toggle". Toggle files only appear in quarterly drops,
    so None is the normal answer on a monthly run — callers skip that stage
    rather than falling back to a previous period's file, which is how the
    202608 toggle output kept being rewritten during the 202609 run.
    """
    if not PERIOD:
        return None
    tag = INSURERS.get(insurer, insurer.upper())
    hits = [
        p for p in glob.glob(os.path.join(data_dir(insurer), f"{product}_{tag}_*.csv"))
        if "_chunk_" not in os.path.basename(p)
    ]
    toggles = [p for p in hits if p.endswith("_toggle.csv")]
    if kind == "toggle":
        return sorted(toggles)[0] if toggles else None
    mains = [p for p in hits if p not in toggles]
    # `*_main.csv` wins when both spellings exist; Youi Motor drops a bare
    # `Motor_YOUI_<period>.csv` with no suffix at all, so that is the fallback.
    preferred = [p for p in mains if p.endswith("_main.csv")] or mains
    return sorted(preferred)[0] if preferred else None


def output_csv(insurer: str, product: str, kind: str = "main") -> str | None:
    """Where that product's rows land: same basename, under outputs/<period>/."""
    src = input_csv(insurer, product, kind)
    if not src:
        return None
    return os.path.join(out_dir(insurer), os.path.basename(src))


def stem(insurer: str, product: str, kind: str = "main") -> str | None:
    """The input basename without .csv — what Youi's runner calls `filename`."""
    src = input_csv(insurer, product, kind)
    return os.path.basename(src)[:-4] if src else None


def chunks_glob(insurer: str, product: str) -> str | None:
    """Youi fans out over pre-split chunks; chunkify.py writes them."""
    name = stem(insurer, product)
    return os.path.join(data_dir(insurer), "chunks", f"{name}_chunk_*.csv") if name else None


def _rows(path: str | None) -> str:
    if not path or not os.path.exists(path):
        return "-"
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return str(sum(1 for _ in csv.reader(fh)) - 1)


def report() -> int:
    """Print what a launch would collect. The preflight after a data drop."""
    if not PERIOD:
        print("No period found. Drop input CSVs into <Insurer>/inputs/<YYYYMM>/data/")
        return 1
    print(f"period {PERIOD}  (from {PERIOD_SOURCE})   known: {', '.join(periods()) or 'none'}")
    missing = 0
    for insurer in INSURERS:
        for product in PRODUCTS:
            src, dst = input_csv(insurer, product), output_csv(insurer, product)
            if not src:
                continue
            chunks = len(glob.glob(chunks_glob(insurer, product) or ""))
            print(f"  {insurer:5} {product:10} in {_rows(src):>5}  out {_rows(dst):>5}"
                  f"{f'  chunks {chunks}' if chunks else ''}   {os.path.basename(src)}")
            missing += not os.path.exists(src)
    if not any(input_csv(i, p) for i in INSURERS for p in PRODUCTS):
        print(f"  nothing in inputs/{PERIOD}/data — is the drop complete?")
        return 1
    return 1 if missing else 0


def _selftest() -> int:
    import tempfile
    global HERE, PERIOD
    real_here, real_period = HERE, PERIOD
    try:
        with tempfile.TemporaryDirectory() as tmp:
            HERE = tmp
            for name in ("202608", "202609"):
                os.makedirs(os.path.join(tmp, "NRMA", "inputs", name, "data"))
            os.makedirs(os.path.join(tmp, "Youi", "inputs", "202609", "data", "chunks"))
            # A half-prepared drop: no data/ dir, so it must not win.
            os.makedirs(os.path.join(tmp, "RACV", "inputs", "202610", "delivery_template"))
            assert periods() == ["202608", "202609"], periods()

            PERIOD = "202609"
            nrma = os.path.join(tmp, "NRMA", "inputs", "202609", "data")
            for n in ("Contents_NRMA_monthly_202609_main.csv",
                      "Contents_NRMA_quarterly_202609_toggle.csv",
                      "Buildings_NRMA_monthly_202609_main.csv"):
                open(os.path.join(nrma, n), "w").close()
            youi = os.path.join(tmp, "Youi", "inputs", "202609", "data")
            open(os.path.join(youi, "Motor_YOUI_monthly_202609.csv"), "w").close()

            # Read, never built: the cadence word comes back as dropped.
            assert input_csv("NRMA", "Contents").endswith("Contents_NRMA_monthly_202609_main.csv")
            assert input_csv("NRMA", "Contents", "toggle").endswith("_quarterly_202609_toggle.csv")
            assert input_csv("NRMA", "Buildings", "toggle") is None  # monthly drop: skip the stage
            assert input_csv("NRMA", "Motor") is None                # not dropped at all
            # A bare name with no _main suffix still resolves (Youi Motor).
            assert input_csv("Youi", "Motor").endswith("Motor_YOUI_monthly_202609.csv")
            # Output mirrors the input basename, under outputs/<period>/.
            assert output_csv("NRMA", "Contents") == os.path.join(
                tmp, "NRMA", "outputs", "202609", "Contents_NRMA_monthly_202609_main.csv")
            assert output_csv("NRMA", "Motor") is None
            assert stem("NRMA", "Contents") == "Contents_NRMA_monthly_202609_main"
            assert chunks_glob("Youi", "Motor").endswith("Motor_YOUI_monthly_202609_chunk_*.csv")

            PERIOD = ""
            assert input_csv("NRMA", "Contents") is None  # nothing dropped: no crash
    finally:
        HERE, PERIOD = real_here, real_period
    print("ok: period resolution, cadence read from the drop, absent files are None")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else report())
