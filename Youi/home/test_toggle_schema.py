"""A toggle CSV's header must not depend on which row lands first.

write_csv takes the header from the first row it writes. The toggle dicts used
to gain their extras (excess_5000, the three Contents add-ons) only on a priced
quote, so when a decline landed first (202611 Buildings toggle, 20171.2025)
every priced row carried a value with no column name.

Run: python Youi/home/test_toggle_schema.py
"""
import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crawlers import YouiBuildingCrawler, YouiContentsCrawler

EXTRAS = {
    YouiBuildingCrawler: {"excess_5000": "2792.49"},
    YouiContentsCrawler: {"ContentsAccidentalLossOrDamage": "430.1",
                          "ContentsBusinessItems": "441.2",
                          "ContentsInCommercialStorage": "425.3"},
}

with tempfile.TemporaryDirectory() as tmp:
    for cls, extras in EXTRAS.items():
        out = os.path.join(tmp, f"{cls.__name__}.csv")
        decline = cls({"SAMPLE_NUMBER": "1"}, out, toggle=True)
        decline.output_data_dict["Reason_for_quote_failure"] = "bushfire risk"
        decline.write_csv()                       # the decline lands first
        priced = cls({"SAMPLE_NUMBER": "2"}, out, toggle=True)
        priced.output_data_dict.update({"YearlyPayment": "100.0", **extras})
        priced.write_csv()
        with open(out, newline="") as fh:
            header, *rows = list(csv.reader(fh))
        assert all(len(r) == len(header) for r in rows), (cls.__name__, [len(r) for r in rows])
        assert set(extras) <= set(header), (cls.__name__, set(extras) - set(header))
        assert dict(zip(header, rows[1]))["YearlyPayment"] == "100.0"
print("ok")
