"""Sum-insured adjustment shared by building.py, contents.py and combined.py.

Suncorp's method (2026-10-29). When the sample's sum insured (SI) is outside
NRMA's range:
- below it (home too big): re-quote the home stripped (see stripped()). Still
  below: the quote fails. Above: grow it as below.
- above it (home too small): one more large bedroom per re-quote until the
  range takes the SI.
Only the fields the product's lookup marks "Bot allowed to toggle to achieve
desired SI" = Yes change; the rest (pool, storeys, ...) stay as described. A
priced re-quote fills every one of them in its Rerun_ column. A failure reports
the range and the quote of the home as described.

    python NRMA/home/si_steps.py    # offline self-check
"""
import logging

log = logging.getLogger(__name__)

# In 202611 no grow needed more than 7 large bedrooms; a strip is one more.
MAX_SI_REQUOTES = 8

# Lookup input column ("Columns To Refer"; Contents: "Columns Needed") -> NRMA
# answer key. The Profile attribute is the column name lowercased.
API_KEYS = {
    "Adults": "AdultOccupants",
    "Children": "ChildOccupants",
    "Small_Bedrooms": "SmallBedrooms",
    "Medium_Bedrooms": "MediumBedrooms",
    "Large_Bedrooms": "LargeBedrooms",
    "Bathrooms": "Bathrooms",
    "Living_Rooms": "LivingSpaces",
    "Dining_Rooms": "DiningRooms",
    "Carport": "ParkingSpacesCarport",
    "Garage": "ParkingSpacesGarage",
    "Small_Shed": "SmallSheds",
    "Large_Shed": "LargeSheds",
    "Balconies": "BalconiesAndDecks",
    "Verandahs": "Verandahs",
}
# Each product's Yes fields in Rerun_ column order: the delivery template's three
# first, then the rest in lookup order (Suncorp, 2026-10-29).
# test_template_columns.py pins them to the period's lookup files.
BUILDING_FIELDS = (
    "Small_Bedrooms", "Large_Bedrooms", "Carport", "Medium_Bedrooms", "Bathrooms",
    "Garage", "Small_Shed", "Large_Shed", "Balconies", "Verandahs",
)
CONTENTS_FIELDS = (  # Contents and Combined
    "Small_Bedrooms", "Large_Bedrooms", "Carport", "Adults", "Children",
    "Medium_Bedrooms", "Bathrooms", "Living_Rooms", "Dining_Rooms", "Garage",
    "Small_Shed", "Large_Shed", "Balconies", "Verandahs",
)


class SumInsuredOutOfRange(ValueError):
    """Raised when the target sum insured is outside the API's accepted range."""

    def __init__(self, si_type, target, minimum, maximum, direction):
        self.si_type = si_type
        self.target = target
        self.minimum = minimum
        self.maximum = maximum
        self.direction = direction
        super().__init__(
            f"SumInsuredOutOfRange|{si_type}|{direction}|"
            f"target={target}|min={minimum}|max={maximum}"
        )


def stripped(home, target):
    """Suncorp's smallest home: small bedrooms, bathrooms and adults 1, living
    spaces 1 when the SI below its range is $50,000 or more (else 0), every
    other field 0."""
    return {f: int(f in ("Small_Bedrooms", "Bathrooms", "Adults")
                   or f == "Living_Rooms" and target >= 50_000) for f in home}


async def quote(attempt, profile, result, fields):
    """Run attempt(overrides) until NRMA's range takes the sum insured.

    The first call quotes the sample as described (overrides None). After a
    priced re-quote, result.rerun holds that home. Otherwise this raises the
    first SumInsuredOutOfRange, and the row keeps that first quote's number and
    link. Combined checks the building SI first: either SI below its range
    after a re-quote ends the search, and either above gets a large bedroom.
    """
    try:
        return await attempt(None)
    except SumInsuredOutOfRange as exc:
        first = exc
    described = result.quote_number, result.quote_link
    home = {f: getattr(profile, f.lower()) for f in fields}
    grow = first.direction == "high"
    if not grow:
        home = stripped(home, first.target)
    for _ in range(MAX_SI_REQUOTES):
        if grow:
            home["Large_Bedrooms"] += 1
        log.info("│  SI re-quote (%s %s): %s", first.si_type, first.direction, home)
        try:
            await attempt({API_KEYS[f]: v for f, v in home.items()})
        except SumInsuredOutOfRange as exc:
            if exc.direction == "low":  # too big even stripped, or a large bedroom jumped the range
                break
            grow = True
            continue
        result.rerun = {f: str(v) for f, v in home.items()}
        return
    result.quote_number, result.quote_link = described
    raise first


def _demo():
    import asyncio
    from types import SimpleNamespace

    field_of = {k: f for f, k in API_KEYS.items()}

    def run(fields, home, targets, minimums):
        """Quote a fake NRMA: each SI's range is minimum(home)..1.875×minimum(home), building checked first."""
        p = SimpleNamespace(**{f.lower(): home.get(f, 0) for f in fields})
        r = SimpleNamespace(rerun={}, quote_number="", quote_link="")
        seen = []

        async def attempt(overrides):
            h = {field_of[k]: v for k, v in overrides.items()} if overrides else {f: home.get(f, 0) for f in fields}
            seen.append(h)
            r.quote_number = r.quote_link = f"Q{len(seen)}"
            for si_type, target in targets.items():
                lo = minimums[si_type](h)
                hi = lo * 15 // 8
                if not lo <= target <= hi:
                    raise SumInsuredOutOfRange(si_type, target, lo, hi, "low" if target < lo else "high")

        try:
            asyncio.run(quote(attempt, p, r, fields))
            return seen, r.rerun
        except SumInsuredOutOfRange as e:
            assert r.quote_number == r.quote_link == "Q1", "a failed row must point at the as-described quote"
            assert r.rerun == {}, "a failed row has no Rerun values"
            return seen, str(e)

    def bld(h):
        return (200_000 + 60_000 * h["Small_Bedrooms"] + 70_000 * h["Medium_Bedrooms"] + 90_000 * h["Large_Bedrooms"]
                + 40_000 * h["Bathrooms"] + 20_000 * h["Carport"] + 30_000 * h["Garage"]
                + 10_000 * (h["Small_Shed"] + h["Large_Shed"] + h["Balconies"] + h["Verandahs"]))

    def cnt(h):
        return (10_000 + 5_000 * h["Small_Bedrooms"] + 6_000 * h["Medium_Bedrooms"] + 8_000 * h["Large_Bedrooms"]
                + 3_000 * h["Bathrooms"] + 4_000 * h["Living_Rooms"] + 2_000 * h["Dining_Rooms"]
                + 3_000 * h["Adults"] + 2_000 * h["Children"] + 1_000 * h["Garage"])

    big = {"Small_Bedrooms": 4, "Medium_Bedrooms": 2, "Bathrooms": 2, "Garage": 3, "Small_Shed": 1, "Verandahs": 1}

    # In range as described: one quote, Rerun columns stay blank.
    assert run(BUILDING_FIELDS, big, {"building": 900_000}, {"building": bld}) == ([dict.fromkeys(BUILDING_FIELDS, 0) | big], {})
    # Suncorp's 80015 shape: below the range -> strip (now above) -> +1 large -> +2 fits; every Yes field recorded.
    seen, rerun = run(BUILDING_FIELDS, big, {"building": 750_000}, {"building": bld})
    assert [bld(h) for h in seen] == [770_000, 300_000, 390_000, 480_000], seen
    assert rerun == {f: "1" if f in ("Small_Bedrooms", "Bathrooms") else "2" if f == "Large_Bedrooms" else "0"
                     for f in BUILDING_FIELDS}, rerun
    # Still below when stripped: fails after one re-quote, with the as-described range.
    seen, out = run(BUILDING_FIELDS, big, {"building": 250_000}, {"building": bld})
    assert len(seen) == 2 and out == "SumInsuredOutOfRange|building|low|target=250000|min=770000|max=1443750", out
    # Above the range: one large bedroom per re-quote on the home as described.
    small = {"Small_Bedrooms": 1, "Medium_Bedrooms": 1, "Bathrooms": 1, "Garage": 1}
    seen, rerun = run(BUILDING_FIELDS, small, {"building": 1_000_000}, {"building": bld})
    assert [h["Large_Bedrooms"] for h in seen] == [0, 1, 2] and rerun["Garage"] == rerun["Medium_Bedrooms"] == "1", (seen, rerun)
    # Capped at MAX_SI_REQUOTES, then the as-described range.
    seen, out = run(BUILDING_FIELDS, small, {"building": 2_000_000}, {"building": lambda h: 400_000 + 1_000 * h["Large_Bedrooms"]})
    assert len(seen) == 1 + MAX_SI_REQUOTES and out.endswith("min=400000|max=750000"), (seen, out)
    # A large bedroom that jumps the whole range ends the search instead of bouncing.
    seen, out = run(BUILDING_FIELDS, small, {"building": 800_000}, {"building": lambda h: 300_000 + 1_000_000 * h["Large_Bedrooms"]})
    assert len(seen) == 2 and "|building|high|" in out, (seen, out)
    # Contents: the strip keeps one adult, and living spaces follow the $50,000 line.
    family = {"Small_Bedrooms": 2, "Medium_Bedrooms": 1, "Large_Bedrooms": 1, "Bathrooms": 2, "Living_Rooms": 2,
              "Dining_Rooms": 1, "Adults": 2, "Children": 2, "Carport": 1, "Garage": 1}
    seen, rerun = run(CONTENTS_FIELDS, family, {"contents": 40_000}, {"contents": cnt})
    assert seen[1] == stripped(seen[0], 40_000) and seen[1]["Adults"] == 1 and seen[1]["Living_Rooms"] == 0, seen
    assert rerun["Large_Bedrooms"] == "1" and rerun["Adults"] == "1" and rerun["Children"] == "0", rerun
    assert stripped(family, 50_000)["Living_Rooms"] == 1 and stripped(family, 49_999)["Living_Rooms"] == 0
    # Growing never touches the occupants.
    seen, rerun = run(CONTENTS_FIELDS, family, {"contents": 130_000}, {"contents": cnt})
    assert rerun["Adults"] == rerun["Children"] == "2" and rerun["Large_Bedrooms"] == "3", rerun
    # Combined: the building SI strips the home (living 1: building SI >= $50k), then contents is above -> +1 large.
    seen, rerun = run(CONTENTS_FIELDS, {**big, "Adults": 2, "Living_Rooms": 1, "Dining_Rooms": 1},
                      {"building": 500_000, "contents": 50_000}, {"building": bld, "contents": cnt})
    assert len(seen) == 3 and rerun["Living_Rooms"] == "1" and rerun["Large_Bedrooms"] == "1", (seen, rerun)
    print("si_steps self-check ok")


if __name__ == "__main__":
    _demo()
