#!/usr/bin/env python3
"""Remove any row that contained an "Error" cell from all Youi output CSV files.

A valid Youi output row never has an empty data cell (missing values are
written as "N/A"). A previous pass blanked out cells whose value was exactly
"Error"; this script removes any row that now contains a blank cell -- i.e.
the rows that originally had an "Error" -- so those rows are dropped entirely.

Files are rewritten in place; a summary of removed rows is printed.
"""

import csv
import os

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


def drop_error_rows_in_file(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        return [], 0

    header, data = rows[0], rows[1:]
    kept = []
    removed = []
    for row in data:
        if any(cell.strip() == "" for cell in row):
            removed.append(row[0] if row else "")
        else:
            kept.append(row)

    if removed:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(kept)
    return removed, len(data)


def main():
    total_removed = 0
    total_files = 0
    for root, _dirs, files in os.walk(OUTPUTS_DIR):
        for name in files:
            if not name.lower().endswith(".csv"):
                continue
            path = os.path.join(root, name)
            removed, _ = drop_error_rows_in_file(path)
            if removed:
                total_files += 1
                total_removed += len(removed)
                rel = os.path.relpath(path, OUTPUTS_DIR)
                print(f"{rel}: removed {len(removed)} row(s): {', '.join(removed)}")

    print(f"\nDone. Removed {total_removed} row(s) across {total_files} file(s).")


if __name__ == "__main__":
    main()
