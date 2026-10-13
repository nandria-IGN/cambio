from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Dict, Iterable, Optional

from flask import Flask, jsonify, render_template, request


@dataclass(frozen=True)
class Currency:
    alpha: str
    numeric: str
    minor_units: int
    name: str
    symbol: str
    status: str = "active"


class CurrencyCatalog:
    def __init__(self, currencies: Optional[Iterable[Currency]] = None):
        entries = currencies or (
            Currency("AUD", "036", 2, "Dollar australien", "A$"),
            Currency("BHD", "048", 3, "Dinar bahreïni", "BD"),
            Currency("CAD", "124", 2, "Dollar canadien", "CA$"),
            Currency("CHF", "756", 2, "Franc suisse", "CHF"),
            Currency("CLF", "990", 4, "Unité de compte chilienne", "CLF"),
            Currency("CNY", "156", 2, "Yuan renminbi", "CN¥"),
            Currency("DEM", "276", 2, "Deutsche Mark (retiré)", "DM", "withdrawn"),
            Currency("EUR", "978", 2, "Euro", "€"),
            Currency("GBP", "826", 2, "Livre sterling", "£"),
            Currency("JPY", "392", 0, "Yen japonais", "¥"),
            Currency("KWD", "414", 3, "Dinar koweïtien", "KD"),
            Currency("USD", "840", 2, "Dollar américain", "$"),
        )
        self._currencies = {currency.alpha: currency for currency in entries}

    def get(self, code: str) -> Optional[Currency]:
        return self._currencies.get(code.strip().upper())

    def active(self):
        return [currency for currency in self._currencies.values() if currency.status == "active"]

    def all(self):
        return list(self._currencies.values())


@dataclass(frozen=True)
class Money:
    currency: str
    amount_minor: int


@dataclass(frozen=True)
class RateQuote:
    base: str
    rates: Dict[str, str]
    as_of: datetime
    source: str


class RateProvider:
    def latest(self) -> RateQuote:
        raise NotImplementedError


class StaticRateProvider(RateProvider):
    def __init__(self, quote: Optional[RateQuote] = None):
        self._quote = quote or RateQuote(
            base="EUR",
            rates={
                "AUD": "1.6472",
                "BHD": "0.4108",
                "CAD": "1.4821",
                "CHF": "0.9426",
                "CLF": "0.0261",
                "CNY": "7.8642",
                "EUR": "1",
                "GBP": "0.8567",
                "JPY": "162.34",
                "KWD": "0.3348",
                "USD": "1.0874",
            },
            as_of=datetime(2026, 10, 13, 8, tzinfo=timezone.utc),
            source="Jeu de démonstration local",
        )

    def latest(self) -> RateQuote:
        return self._quote


class ConversionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class AmountParser:
    MAX_MAJOR_AMOUNT = 1_000_000_000_000_000
    AMOUNT_PATTERN = re.compile(r"^(?:\d+|\d+[.,]\d+)$")

    def __init__(self, catalog: CurrencyCatalog):
        self._catalog = catalog

    def parse(self, value: str, currency_code: str) -> Money:
        text = value.strip()
        if not text:
            raise ConversionError("EMPTY_AMOUNT", "Saisissez un montant.")
        if text.startswith("-"):
            raise ConversionError("NEGATIVE_AMOUNT", "Les montants négatifs ne sont pas pris en charge.")
        if not self.AMOUNT_PATTERN.fullmatch(text):
            raise ConversionError("INVALID_AMOUNT", "Montant invalide.")

        currency = self._catalog.get(currency_code)
        if currency is None or currency.status != "active":
            raise ConversionError("UNSUPPORTED_CURRENCY", "Devise non prise en charge.")

        normalized = text.replace(",", ".")
        whole_text, separator, fraction = normalized.partition(".")
        if len(fraction) > currency.minor_units:
            raise ConversionError(
                "INVALID_PRECISION",
                "Maximum {} décimale{} pour {}.".format(
                    currency.minor_units,
                    "" if currency.minor_units == 1 else "s",
                    currency.alpha,
                ),
            )

        whole = int(whole_text)
        if whole > self.MAX_MAJOR_AMOUNT:
            raise ConversionError("AMOUNT_TOO_LARGE", "Montant trop élevé.")
        scale = 10 ** currency.minor_units
        amount_minor = whole * scale + int(fraction.ljust(currency.minor_units, "0") or "0")
        if amount_minor > self.MAX_MAJOR_AMOUNT * scale:
            raise ConversionError("AMOUNT_TOO_LARGE", "Montant trop élevé.")
        return Money(currency.alpha, amount_minor)


class ConversionService:
    def __init__(
        self,
        catalog: CurrencyCatalog,
        provider: RateProvider,
        stale_after: timedelta = timedelta(hours=24),
    ):
        self._catalog = catalog
        self._provider = provider
        self._stale_after = stale_after

    def convert(self, amount: Money, target_code: str, now: Optional[datetime] = None):
        source = self._catalog.get(amount.currency)
        target = self._catalog.get(target_code)
        if source is None or target is None or source.status != "active" or target.status != "active":
            raise ConversionError("UNSUPPORTED_CURRENCY", "Devise non prise en charge.")
        if amount.amount_minor < 0:
            raise ConversionError("NEGATIVE_AMOUNT", "Les montants négatifs ne sont pas pris en charge.")

        quote = self._provider.latest()
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        is_stale = current_time - quote.as_of > self._stale_after

        if source.alpha == target.alpha:
            return self._result(amount, Money(target.alpha, amount.amount_minor), "1", quote, is_stale)

        try:
            source_rate = Decimal(quote.rates[source.alpha])
            target_rate = Decimal(quote.rates[target.alpha])
            if not source_rate.is_finite() or not target_rate.is_finite() or source_rate <= 0 or target_rate <= 0:
                raise InvalidOperation
        except (KeyError, InvalidOperation, ValueError):
            raise ConversionError("MISSING_RATE", "Taux indisponible pour cette paire.")

        with localcontext() as context:
            context.prec = 60
            rate = target_rate / source_rate
            source_scale = Decimal(10 ** source.minor_units)
            target_scale = Decimal(10 ** target.minor_units)
            target_minor = (
                Decimal(amount.amount_minor) * rate * target_scale / source_scale
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            rate_display = format(rate.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP), "f").rstrip("0").rstrip(".")

        return self._result(amount, Money(target.alpha, int(target_minor)), rate_display, quote, is_stale)

    @staticmethod
    def _result(source: Money, target: Money, rate: str, quote: RateQuote, is_stale: bool):
        return {
            "from": {"currency": source.currency, "amount_minor": str(source.amount_minor)},
            "to": {"currency": target.currency, "amount_minor": str(target.amount_minor)},
            "rate_used": rate,
            "as_of": quote.as_of.isoformat(),
            "source": quote.source,
            "is_stale": is_stale,
            "is_indicative": True,
        }


def create_app(converter: Optional[ConversionService] = None, catalog: Optional[CurrencyCatalog] = None):
    app = Flask(__name__)
    currency_catalog = catalog or CurrencyCatalog()
    amount_parser = AmountParser(currency_catalog)
    conversion_service = converter or ConversionService(currency_catalog, StaticRateProvider())

    @app.route("/", methods=["GET"])
    def home():
        return render_template("index.html", currencies=currency_catalog.active())

    @app.route("/api/currencies", methods=["GET"])
    def currencies():
        return jsonify([
            {
                "alpha": currency.alpha,
                "numeric": currency.numeric,
                "minor_units": currency.minor_units,
                "name": currency.name,
                "symbol": currency.symbol,
                "status": currency.status,
            }
            for currency in currency_catalog.all()
        ])

    @app.route("/api/convert", methods=["POST"])
    def convert():
        payload = request.get_json(silent=True) or {}
        try:
            source_code = str(payload.get("source", ""))
            target_code = str(payload.get("target", ""))
            amount = amount_parser.parse(str(payload.get("amount", "")), source_code)
            return jsonify(conversion_service.convert(amount, target_code))
        except ConversionError as error:
            return jsonify({"error": error.message, "code": error.code}), 400

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)