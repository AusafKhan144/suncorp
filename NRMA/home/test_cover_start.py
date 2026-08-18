# Cover start / input validation checks.
#   python NRMA/home/test_cover_start.py
#
# Guards the two failure modes that silently corrupted a delivery: a
# Policy_Effective_Date the parser did not understand becoming date.today(),
# and an unusable CSV row being dropped with a log warning.
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nrma_browser import cover_start_date  # noqa: E402
import contents  # noqa: E402


def test_both_csv_spellings():
    assert cover_start_date("31-Aug-2026") == "2026-08-31"   # monthly files
    assert cover_start_date("23-Jul-26") == "2026-07-23"     # quarterly files
    assert cover_start_date("  7-May-2026 ") == "2026-05-07"


def test_bad_dates_raise():
    for bad in ("", "   ", None, "2026-08-31", "31/08/2026", "Aug-2026", "not a date"):
        try:
            cover_start_date(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not have parsed")


def test_load_profiles_raises_on_bad_row():
    header = (
        "Sample_Number,Policy_Effective_Date,Building_Type,Address,Suburb,"
        "Postcode,State,Contents_Sum_Insured,Live_With,Cover\n"
    )
    good = "1.2025,23-Jul-26,Free standing house,1 A ST,SYLVANIA,2224,NSW,100000,Myself or family,Contents Insurance\n"
    bad = "2.2025,,Free standing house,2 A ST,SYLVANIA,2224,NSW,100000,Myself or family,Contents Insurance\n"

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(header + good)
        clean = fh.name
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(header + good + bad)
        dirty = fh.name

    profiles = contents.load_profiles(clean)
    assert len(profiles) == 1, profiles
    assert profiles[0].policy_effective_date == "2026-07-23"

    try:
        contents.load_profiles(dirty)
    except ValueError as exc:
        assert "2.2025" in str(exc), exc          # names the offending row
        assert "1 unusable row" in str(exc), exc
    else:
        raise AssertionError("a blank Policy_Effective_Date must fail the run")

    os.unlink(clean)
    os.unlink(dirty)


if __name__ == "__main__":
    test_both_csv_spellings()
    test_bad_dates_raise()
    test_load_profiles_raises_on_bad_row()
    print("ok")
