"""Example unit tests for the phone, date, and country normalizers (Task 3.1).

These verify concrete representative inputs (including the messy sample-fixture
values) and the documented quality scoring. The universal "canonical-or-null"
property is covered separately by the property-based test (Task 3.4).
"""

from __future__ import annotations

from candidate_transformer.normalizers import (
    normalize_country,
    normalize_date,
    normalize_phone,
)


class TestNormalizePhone:
    def test_explicit_country_code_is_high_quality(self):
        assert normalize_phone("+44 20 7946 0000") == ("+442079460000", 1.0)

    def test_assumed_region_is_reduced_quality(self):
        # No explicit "+" so the default region must be assumed -> 0.7.
        assert normalize_phone("(415) 555-2671", default_region="US") == (
            "+14155552671",
            0.7,
        )

    def test_already_e164_is_idempotent_and_high_quality(self):
        assert normalize_phone("+14155552671") == ("+14155552671", 1.0)

    def test_unparseable_is_null(self):
        assert normalize_phone("not-a-phone") == (None, 0.0)

    def test_no_region_and_no_country_code_is_null(self):
        # Cannot parse a national number without a region to anchor it.
        assert normalize_phone("4155552671") == (None, 0.0)

    def test_empty_and_non_string_are_null(self):
        assert normalize_phone("") == (None, 0.0)
        assert normalize_phone("   ") == (None, 0.0)
        assert normalize_phone(None) == (None, 0.0)
        assert normalize_phone(1234567890) == (None, 0.0)


class TestNormalizeDate:
    def test_month_name_and_year(self):
        assert normalize_date("March 2019") == ("2019-03", 1.0)
        assert normalize_date("Mar 2019") == ("2019-03", 1.0)
        assert normalize_date("2019 March") == ("2019-03", 1.0)

    def test_iso_year_month(self):
        assert normalize_date("2019-03") == ("2019-03", 1.0)

    def test_full_iso_date_drops_day(self):
        assert normalize_date("2019-03-15") == ("2019-03", 1.0)

    def test_numeric_month_year(self):
        assert normalize_date("03/2019") == ("2019-03", 1.0)
        assert normalize_date("03-2019") == ("2019-03", 1.0)

    def test_year_only_assumes_january_at_reduced_quality(self):
        assert normalize_date("2019") == ("2019-01", 0.6)

    def test_out_of_range_month_is_null(self):
        assert normalize_date("2019-13") == (None, 0.0)

    def test_unparseable_is_null(self):
        assert normalize_date("garbage") == (None, 0.0)
        assert normalize_date("Notamonth 2019") == (None, 0.0)

    def test_empty_and_non_string_are_null(self):
        assert normalize_date("") == (None, 0.0)
        assert normalize_date(None) == (None, 0.0)
        assert normalize_date(2019) == (None, 0.0)


class TestNormalizeCountry:
    def test_exact_alpha2_code(self):
        assert normalize_country("US") == ("US", 1.0)
        assert normalize_country("gb") == ("GB", 1.0)

    def test_exact_alpha3_code(self):
        assert normalize_country("USA") == ("US", 1.0)

    def test_exact_name(self):
        assert normalize_country("United States") == ("US", 1.0)
        assert normalize_country("india") == ("IN", 1.0)
        assert normalize_country("United Kingdom") == ("GB", 1.0)

    def test_unresolvable_is_null(self):
        assert normalize_country("Atlantis") == (None, 0.0)

    def test_empty_and_non_string_are_null(self):
        assert normalize_country("") == (None, 0.0)
        assert normalize_country(None) == (None, 0.0)
        assert normalize_country(42) == (None, 0.0)
