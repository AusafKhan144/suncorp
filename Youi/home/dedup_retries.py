"""Drop retry duplicates from home output CSVs before delivery.

A resumed or re-run crawl appends a fresh row per sample instead of replacing
the old one, so outputs carry more rows than the input has samples. Delivery
expects one row per sample (every prior run shipped exactly input-row-count).

Keep the first successful attempt per sample; fall back to the first failure
when every attempt failed. Original row order is preserved.

    python dedup_retries.py 20260817 [--dry-run]
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SUCCESS = "N/A"  # Reason_for_quote_failure sentinel for "quote collected"


def _read_text(path: Path) -> pd.DataFrame:
    """Read as raw text so literal 'N/A' cells survive the round-trip."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def dedup(df: pd.DataFrame) -> pd.DataFrame:
    """One row per sample_number: first success, else first failure."""
    order = pd.RangeIndex(len(df))
    ranked = df.assign(
        _ok=(df["Reason_for_quote_failure"] == SUCCESS).astype(int),
        _i=order,
    ).sort_values(["_ok", "_i"], ascending=[False, True])
    kept = ranked.drop_duplicates("sample_number", keep="first")
    return kept.sort_values("_i").drop(columns=["_ok", "_i"])


def process(in_csv: Path, out_csv: Path, dry_run: bool = False) -> None:
    src = _read_text(in_csv)
    dst = _read_text(out_csv)
    kept = dedup(dst)

    sample_col = next(c for c in src.columns if c.lower() == "sample_number")
    expected = src[sample_col].nunique()
    if len(kept) != expected:
        raise SystemExit(
            f"{out_csv.name}: {len(kept)} rows after dedup, input has {expected} samples"
        )

    # failures write the literal string "N/A" here, successes write a number
    priced = pd.to_numeric(kept["YearlyPayment"], errors="coerce").notna().sum()
    print(
        f"{out_csv.name}: {len(dst)} -> {len(kept)} rows "
        f"({len(dst) - len(kept)} retry dupes dropped), {priced} priced"
    )
    if dry_run:
        return

    raw = out_csv.read_bytes()
    backup = out_csv.with_suffix(".csv.pre_dedup")
    if not backup.exists():
        backup.write_bytes(raw)
    # The crawler writes CRLF; pandas defaults to LF and would rewrite every line.
    eol = "\r\n" if b"\r\n" in raw.split(b"\n", 1)[0] + b"\n" else "\n"
    kept.to_csv(out_csv, index=False, lineterminator=eol)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    run = args[0]
    dry_run = "--dry-run" in sys.argv

    for out_csv in sorted((ROOT / "outputs" / run).glob("*_main.csv")):
        in_csv = ROOT / "inputs" / run / "data" / out_csv.name
        if not in_csv.exists():
            print(f"skip {out_csv.name}: no matching input")
            continue
        process(in_csv, out_csv, dry_run)


def demo() -> None:
    """Self-check: success wins over failure, order preserved, N/A survives."""
    df = pd.DataFrame({
        "sample_number": ["1", "2", "1", "3", "2", "3"],
        "YearlyPayment": ["", "500", "100", "", "501", ""],
        "Reason_for_quote_failure": [
            "Address Not Found", SUCCESS, SUCCESS,     # 1 fails then succeeds
            "Address Not Found", SUCCESS, "Address Not Found",  # 3 never succeeds
        ],
    })
    kept = dedup(df)
    assert kept["sample_number"].tolist() == ["2", "1", "3"], kept
    # sample 1 keeps its successful retry, not the earlier failure
    assert kept.set_index("sample_number").loc["1", "YearlyPayment"] == "100"
    # sample 2 keeps the FIRST success, not the later re-quote
    assert kept.set_index("sample_number").loc["2", "YearlyPayment"] == "500"
    # sample 3 has no success at all: keep its first failure row
    assert kept.set_index("sample_number").loc["3", "Reason_for_quote_failure"] == "Address Not Found"
    print("demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
