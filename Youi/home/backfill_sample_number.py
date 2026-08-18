"""Backfill the sample_number column on home outputs whose runs predate the
SAMPLE_NUMBER canonicalisation fix in runner.load_input_data.

Match key is (postcode, DOB day/month/year) — unique per input row in the
monthly extract, so the mapping is unambiguous. Retry duplicates in the output
share a key and correctly get the same sample number.

    python backfill_sample_number.py 20260817 [--dry-run]
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
IN_KEY = ["postcode", "DOB_dummy_day", "DOB_dummy_month", "DOB_dummy_year"]
OUT_KEY = ["Postcode", "DOB_day", "DOB_month", "DOB_year"]


def _read_text(path: Path) -> pd.DataFrame:
    """Read as raw text so literal 'N/A' cells survive the round-trip."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _numeric_key(df: pd.DataFrame, cols: list[str]) -> pd.MultiIndex:
    """Key on numbers, not text, so '12' and '12.0' still match."""
    return pd.MultiIndex.from_frame(df[cols].apply(pd.to_numeric, errors="coerce"))


def backfill(in_csv: Path, out_csv: Path, dry_run: bool = False) -> None:
    src = _read_text(in_csv)
    dst = _read_text(out_csv)

    sample_col = next(c for c in src.columns if c.lower() == "sample_number")
    lookup = pd.Series(src[sample_col].values, index=_numeric_key(src, IN_KEY))
    if lookup.index.duplicated().any():
        raise SystemExit(f"{in_csv.name}: match key is not unique, cannot backfill")

    filled = _numeric_key(dst, OUT_KEY).map(lookup)

    missing = int(pd.isna(filled).sum())
    if missing:
        raise SystemExit(f"{out_csv.name}: {missing} rows have no match in {in_csv.name}")

    was_blank = int((dst["sample_number"] == "").sum())
    dst["sample_number"] = filled
    print(f"{out_csv.name}: {len(dst)} rows, filled {was_blank} blank sample_number")

    if dry_run:
        print(dst[["sample_number"] + OUT_KEY].head().to_string(index=False))
        return

    raw = out_csv.read_bytes()
    backup = out_csv.with_suffix(".csv.pre_samplenumber")
    if not backup.exists():
        backup.write_bytes(raw)
    # The crawler writes CRLF; pandas defaults to LF and would rewrite every line.
    eol = "\r\n" if b"\r\n" in raw.split(b"\n", 1)[0] + b"\n" else "\n"
    dst.to_csv(out_csv, index=False, lineterminator=eol)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    run = args[0]

    for out_csv in sorted((ROOT / "outputs" / run).glob("*_main.csv")):
        in_csv = ROOT / "inputs" / run / "data" / out_csv.name
        if not in_csv.exists():
            print(f"skip {out_csv.name}: no matching input")
            continue
        backfill(in_csv, out_csv, dry_run)


def demo() -> None:
    """Self-check: round-trip a tiny pair of frames through backfill()."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pd.DataFrame({
            "Sample_Number": [11.2025, 22.2025],
            "postcode": [2000, 3000],
            "DOB_dummy_day": [1, 2],
            "DOB_dummy_month": [3, 4],
            "DOB_dummy_year": [1980, 1990],
        }).to_csv(tmp / "in.csv", index=False)
        # second row is a retry duplicate of the first output row
        pd.DataFrame({
            "sample_number": [None, None, None],
            "Postcode": [3000, 2000, 2000],
            "DOB_day": [2, 1, 1],
            "DOB_month": [4, 3, 3],
            "DOB_year": [1990, 1980, 1980],
            "note": ["N/A"] * 3,
        }).to_csv(tmp / "out.csv", index=False)

        backfill(tmp / "in.csv", tmp / "out.csv")
        out = _read_text(tmp / "out.csv")
        assert out["sample_number"].tolist() == ["22.2025", "11.2025", "11.2025"], out
        assert out["note"].tolist() == ["N/A"] * 3, "literal N/A must survive"
        assert (tmp / "out.csv.pre_samplenumber").exists()
    print("demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
