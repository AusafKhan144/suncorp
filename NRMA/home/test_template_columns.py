# test_template_columns.py — the NRMA scrapers write the client's delivery template.
# Run: python3 test_template_columns.py   (this period; SUNCORP_PERIOD=YYYYMM for another)
#
# Suncorp, 2026-10-22: "use the column names specified in the delivery_template".
# Suncorp, 2026-10-29: plus a Rerun_ column per field the lookup marks "Bot allowed
# to toggle to achieve desired SI" = Yes, after the template's three.
# Run it on every new drop, before collecting: a template or lookup change fails here.
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import openpyxl

import building
import combined
import contents
import period

INPUTS = os.path.join(period.HERE, "NRMA", "inputs", period.PERIOD)


def sheets(path):
    """Every sheet of one workbook as a list of rows."""
    out = {}
    for ws in openpyxl.load_workbook(path, read_only=True).worksheets:
        ws.reset_dimensions()  # the client's files declare A1 as their extent; read_only would stop there
        out[ws.title] = list(ws.iter_rows(values_only=True))
    return out


def template_headers(product):
    """{sheet name: header} from this period's template for one product."""
    paths = glob.glob(os.path.join(INPUTS, "delivery_template", f"delivery_template_{product}_*.xlsx"))
    assert len(paths) == 1, f"{product}: expected one template for {period.PERIOD}, found {paths}"
    return {name: [h for h in rows[0] if h is not None] for name, rows in sheets(paths[0]).items() if rows}


def lookup_yes(name):
    """Input columns this period's lookup marks "Bot allowed to toggle to achieve desired SI" = Yes."""
    header, *rows = sheets(os.path.join(INPUTS, "lookup", f"{name}_lookup_NRMA.xlsx"))["lookup"]
    col = header.index("Columns Needed" if "Columns Needed" in header else "Columns To Refer")  # Contents names it differently
    yes = header.index("Bot allowed to toggle to achieve desired SI")
    return {r[col] for r in rows if len(r) > yes and str(r[yes]).strip().lower() == "yes"}


checked = 0
for module, product, lookup in ((building, "Buildings", "building"), (contents, "Contents", "content"),
                                (combined, "Combined", "combined")):
    if not period.input_csv("NRMA", product):
        print(f"  --  {product}: not in this drop")
        continue
    yes = lookup_yes(lookup)
    assert set(module.SI_FIELDS) == yes, (
        f"{module.__name__} SI_FIELDS != {lookup} lookup's Yes fields: "
        f"missing {sorted(yes - set(module.SI_FIELDS))}, extra {sorted(set(module.SI_FIELDS) - yes)}")
    headers = template_headers(product)
    for kind, fields, to_row in (
        ("main", module.MAIN_FIELDS, module.to_main_row),
        ("toggle", getattr(module, "TOGGLE_FIELDS", None), getattr(module, "to_toggle_row", None)),
    ):
        sheet = f"{product}_{kind}"
        if kind == "toggle" and not period.input_csv("NRMA", product, "toggle"):
            continue  # monthly drop, and Combined: no toggle stage runs
        assert sheet in headers, f"{product}: template has no {sheet} sheet ({sorted(headers)})"
        want = headers[sheet] + [c for c in (f"Rerun_{f}" for f in module.SI_FIELDS) if c not in headers[sheet]]
        assert fields == want, (
            f"{module.__name__} {kind} != template {sheet} + Rerun columns: "
            f"missing {[c for c in want if c not in fields]}, "
            f"extra {[c for c in fields if c not in want]}, or order differs")
        row = to_row(module.QuoteResult(sample_number="1"))
        assert set(row) == set(fields), f"{module.__name__} {kind} row keys != columns: {set(row) ^ set(fields)}"
        checked += 1
        print(f"  ok  {sheet} ({len(headers[sheet])} template + {len(fields) - len(headers[sheet])} Rerun columns)")

assert checked, f"no NRMA input in the {period.PERIOD} drop"
print(f"all {checked} NRMA outputs match the {period.PERIOD} delivery templates and lookups")
