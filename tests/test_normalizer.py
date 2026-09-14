"""Tests for app.services.normalizer"""

import pytest
from decimal import Decimal
from app.services.normalizer import normalize_text, parse_number, amount_difference


class TestNormalizeText:

    def test_none_returns_none(self):
        assert normalize_text(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_text("") is None
        assert normalize_text("   ") is None

    def test_case_folded(self):
        assert normalize_text("USD") == "usd"
        assert normalize_text("Acme Supplies") == "acme supplies"

    def test_whitespace_collapsed(self):
        assert normalize_text("  Acme   Supplies  ") == "acme supplies"

    def test_full_width_chars_normalised(self):
        assert normalize_text("１２３") == "123"
        assert normalize_text("－") == "-"

    def test_trailing_punctuation_removed(self):
        assert normalize_text("Subtotal:") == "subtotal"

    def test_no_strip_punct_mode(self):
        assert normalize_text("Subtotal:", strip_punct=False) == "subtotal:"

    def test_all_whitespace_tokens_collapsed(self):
        assert normalize_text("hello  world\n\n  there") == "hello world there"


class TestParseNumber:

    def test_none_returns_none(self):
        assert parse_number(None) is None

    def test_empty_returns_none(self):
        assert parse_number("") is None
        assert parse_number("   ") is None

    def test_simple_int(self):
        assert parse_number("2") == Decimal("2")

    def test_simple_decimal(self):
        assert parse_number("1000.00") == Decimal("1000.00")

    def test_comma_thousands(self):
        assert parse_number("2,000.00") == Decimal("2000.00")
        assert parse_number("1,234,567") == Decimal("1234567")

    def test_european_comma_decimal(self):
        """Single comma followed by 2 digits → European decimal."""
        assert parse_number("2000,00") == Decimal("2000.00")

    def test_currency_symbol_stripped(self):
        assert parse_number("$150.00") == Decimal("150.00")
        assert parse_number("€ 150.00") == Decimal("150.00")

    def test_negative_in_parens(self):
        assert parse_number("(10.50)") == Decimal("-10.50")

    def test_negative_sign(self):
        assert parse_number("-5") == Decimal("-5")

    def test_decimal_passthrough(self):
        assert parse_number(Decimal("3.14")) == Decimal("3.14")

    def test_float_passthrough(self):
        assert parse_number(2.5) == Decimal("2.5")

    def test_non_numeric_returns_none(self):
        assert parse_number("N/A") is None
        assert parse_number("hello") is None


class TestAmountDifference:

    def test_zero_difference(self):
        assert amount_difference(Decimal("100"), Decimal("100")) == Decimal("0")

    def test_nonzero_difference(self):
        assert amount_difference(Decimal("100"), Decimal("105")) == Decimal("5")

    def test_with_none(self):
        assert amount_difference(Decimal("100"), None) is None
        assert amount_difference(None, Decimal("100")) is None