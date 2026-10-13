import unittest
from datetime import datetime, timedelta, timezone

from app import (
    AmountParser,
    ConversionError,
    ConversionService,
    CurrencyCatalog,
    Money,
    RateQuote,
    StaticRateProvider,
    create_app,
)


class CurrencyDomainTests(unittest.TestCase):
    def setUp(self):
        self.catalog = CurrencyCatalog()
        self.parser = AmountParser(self.catalog)
        self.service = ConversionService(self.catalog, StaticRateProvider())

    def test_currency_catalog_tracks_iso_precision_and_retired_codes(self):
        self.assertEqual(self.catalog.get("JPY").minor_units, 0)
        self.assertEqual(self.catalog.get("EUR").minor_units, 2)
        self.assertEqual(self.catalog.get("KWD").minor_units, 3)
        self.assertEqual(self.catalog.get("DEM").status, "withdrawn")

    def test_amount_parser_uses_integer_minor_units(self):
        self.assertEqual(self.parser.parse("12,345", "KWD"), Money("KWD", 12345))
        self.assertEqual(self.parser.parse("1500", "JPY"), Money("JPY", 1500))

    def test_amount_parser_rejects_bad_precision_negative_and_overflow(self):
        for value, code in [("1.2", "JPY"), ("-1", "EUR"), ("1..2", "EUR"), ("1000000000000001", "EUR")]:
            with self.subTest(value=value, code=code):
                with self.assertRaises(ConversionError):
                    self.parser.parse(value, code)

    def test_converts_between_currencies_with_different_minor_units(self):
        yen = self.service.convert(Money("JPY", 1500), "EUR")
        kwd = self.service.convert(Money("EUR", 1234), "KWD")
        self.assertEqual(yen["to"]["amount_minor"], "924")
        self.assertEqual(kwd["to"]["amount_minor"], "4131")

    def test_same_currency_is_an_exact_identity(self):
        converted = self.service.convert(Money("KWD", 1234), "KWD")
        self.assertEqual(converted["to"]["amount_minor"], "1234")
        self.assertEqual(converted["rate_used"], "1")

    def test_retired_currency_and_missing_rate_are_rejected(self):
        with self.assertRaisesRegex(ConversionError, "Devise non prise en charge"):
            self.service.convert(Money("DEM", 10), "EUR")
        quote = RateQuote("EUR", {"EUR": "1"}, datetime.now(timezone.utc), "test")
        service = ConversionService(self.catalog, StaticRateProvider(quote))
        with self.assertRaisesRegex(ConversionError, "Taux indisponible"):
            service.convert(Money("EUR", 100), "JPY")

    def test_stale_rates_are_reported(self):
        quote = RateQuote("EUR", {"EUR": "1", "USD": "1.08"}, datetime(2020, 1, 1, tzinfo=timezone.utc), "test")
        service = ConversionService(self.catalog, StaticRateProvider(quote))
        result = service.convert(Money("EUR", 100), "USD", datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertTrue(result["is_stale"])
        self.assertTrue(result["is_indicative"])


class CurrencyApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app().test_client()

    def test_home_and_currency_catalog_are_available(self):
        self.assertEqual(self.app.get("/").status_code, 200)
        response = self.app.get("/api/currencies")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(currency["alpha"] == "DEM" for currency in response.get_json()))

    def test_convert_endpoint_returns_exact_minor_units(self):
        response = self.app.post("/api/convert", json={"amount": "1500", "source": "JPY", "target": "EUR"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["to"]["amount_minor"], "924")

    def test_convert_endpoint_returns_a_user_safe_validation_error(self):
        response = self.app.post("/api/convert", json={"amount": "-2", "source": "EUR", "target": "JPY"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "NEGATIVE_AMOUNT")


if __name__ == "__main__":
    unittest.main()