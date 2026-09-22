"""A fractional prefill storey_count must fall back to the CSV, not be sent.

NRMA's property reference API returns `storey_count: 1.5` for split-level
homes. Forwarding "1.5" as `Storeys` made the backend answer the occupancy
POST with an anonymous severity=error validation and drop the party, so
every later call returned no excess and no premium — all 17 unpriced 202609
Contents rows (verified live 2026-09-22 on 20169, 41202 and 70643).

    python NRMA/home/test_prefill_storeys.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from NRMA.home.contents import _prefill_property_attrs, _validation_errors, _whole_storeys

assert _whole_storeys(1.5) is None
assert _whole_storeys("1.5") is None
assert _whole_storeys(0) is None
assert _whole_storeys(None) is None
assert _whole_storeys(2) == "2"
assert _whole_storeys("2.0") == "2"
assert _whole_storeys(4) == "4"

# The live prefill shape for 8 FAIRWAY DR TERRIGAL (2026-09-22).
attrs = _prefill_property_attrs({"storey_count": 1.5, "roof_material": "CONCRETE_TILE"})
assert "Storeys" not in attrs, attrs          # -> CSV Storeys_If_House is used
assert attrs["RoofMaterial"] == "CONCRETE_TILE"
assert _prefill_property_attrs({"storey_count": 2})["Storeys"] == "2"

# The refusal is named, never reported as "no validations".
assert _validation_errors({"validations": [{"severity": "error"}]}) == "error-without-rule_id"
assert _validation_errors({"validations": [{"severity": "error", "rule_id": "VRPP0100"}]}) == "VRPP0100"
assert _validation_errors({"validations": [{"severity": "warning", "rule_id": "VR0207"}]}) == ""
print("ok: fractional storey_count falls back to CSV; anonymous errors are named")
