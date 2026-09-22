"""Check page 1 walks the address candidates instead of branding a sample unquotable.

Two real failure modes are covered:
  * getAddress 400s intermittently  -> re-search and try again
  * the quote's addressSelector rejects a resolvable address (a ranged street
    number like '1177-1185')        -> try the next Experian candidate

Run: python Youi/home/test_address_retry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BaseYouiCrawler, YouiMappings

RANGED = {"moniker": "m0", "address": "1177-1185 Stockleigh Road, LOGAN VILLAGE QLD 4207"}
SINGLE = {"moniker": "m1", "address": "1177 Stockleigh Road, LOGAN VILLAGE QLD 4207"}
QUOTE_OK = {"currentStep": {"stepName": "provisionalHomeDetails"}, "quoteData": {"quoteNumber": "1"}}
INPUT = {
    "suburb": "Logan Village", "postcode": "4207", "state": "Qld",
    "address": "1177-1185 Stockleigh Road", "cover": "Building",
    "BuildingType_1": "Free Standing Home", "BuildingType_2": "Concrete Slab",
    "OccupancyType": "Owner Occupied", "strata": "No",
}


class FakeCrawler(BaseYouiCrawler):
    def __init__(self, accepts, get_address_fails=0):
        super().__init__()
        self.accepts = accepts              # candidate addresses the quote will take
        self.get_address_fails = get_address_fails
        self.input_data = dict(INPUT)
        self.mappings = YouiMappings(self.input_data)
        self.quote_url = "https://quote.youi.com.au/api/quote"
        self.searches = self.puts = 0
        self.rows = []

    def make_request(self, url, method="GET", payload=None, **kw):
        if "experian/search" in url:
            self.searches += 1
            return [RANGED, SINGLE]
        if "getAddress" in url:
            if self.get_address_fails:
                self.get_address_fails -= 1
                return None                 # what a 400 from getAddress returns
            return {"streetNumber": payload["moniker"], "streetName": "STOCKLEIGH ROAD"}
        self.puts += 1
        selected = payload["quoteData"]["propertyAddress"]["experianSearchResult"]["address"]
        return QUOTE_OK if selected in self.accepts else None  # None == addressSelector rejected

    def write_csv(self):
        self.rows.append(dict(self.output_data_dict))


# 1. transient getAddress 400 on every candidate: re-search, then quote the first one
c = FakeCrawler(accepts=[RANGED["address"]], get_address_fails=2)
assert c.populate_page_01()["currentStep"] == "provisionalHomeDetails"
assert c.property_address["experianSearchResult"] == RANGED
assert c.searches == 2 and c.puts == 1, (c.searches, c.puts)
assert c.rows == []

# 2. quote rejects the ranged address: fall through to the single-number candidate
c = FakeCrawler(accepts=[SINGLE["address"]])
assert c.populate_page_01()["currentStep"] == "provisionalHomeDetails"
assert c.property_address["experianSearchResult"] == SINGLE
assert c.searches == 1 and c.puts == 2, (c.searches, c.puts)
assert c.rows == []

# 3. nothing accepted: one failure row, and the step fails loudly
c = FakeCrawler(accepts=[])
try:
    c.populate_page_01()
    raise AssertionError("expected RuntimeError")
except RuntimeError:
    pass
assert c.message == "Address Not Found"
assert len(c.rows) == 1, c.rows  # exactly one row, not two
assert c.rows[0]["Reason_for_quote_failure"] == "Address Not Found"

print("ok")
