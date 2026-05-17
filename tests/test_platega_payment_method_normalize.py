from __future__ import annotations

import pytest

from bloobcat.services.platega import (
    PlategaCreateResult,
    PlategaStatusResult,
    normalize_platega_payment_method,
)


class TestNormalizePlategaPaymentMethod:
    def test_none_returns_none(self):
        assert normalize_platega_payment_method(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_platega_payment_method("") is None
        assert normalize_platega_payment_method("   ") is None

    def test_trims_and_uppercases(self):
        assert normalize_platega_payment_method("  CRypto  ") == "CRYPTO"
        assert normalize_platega_payment_method("sbpqr") == "SBPQR"

    def test_caps_to_32_chars_to_fit_db_column(self):
        long_value = "x" * 100
        result = normalize_platega_payment_method(long_value)
        assert result is not None
        assert len(result) == 32

    def test_accepts_non_string_values_via_str_coercion(self):
        # При фиксированном PLATEGA_PAYMENT_METHOD Платега может вернуть число.
        assert normalize_platega_payment_method(13) == "13"


class TestPlategaResultDataclassesAreBackwardCompatible:
    """payment_method остаётся опциональным — старые вызовы PlategaCreateResult/
    PlategaStatusResult без него не должны падать (важно для тестов и legacy)."""

    def test_create_result_defaults_payment_method_to_none(self):
        result = PlategaCreateResult(
            transaction_id="tx-1",
            status="PENDING",
            redirect_url="https://pay.platega.io/x",
            raw={},
        )
        assert result.payment_method is None

    def test_status_result_defaults_payment_method_to_none(self):
        result = PlategaStatusResult(
            transaction_id="tx-1",
            status="CONFIRMED",
            amount=100.0,
            currency="RUB",
            payload=None,
            raw={},
        )
        assert result.payment_method is None

    def test_create_result_accepts_explicit_payment_method(self):
        result = PlategaCreateResult(
            transaction_id="tx-1",
            status="PENDING",
            redirect_url="https://pay.platega.io/x",
            raw={},
            payment_method="CRYPTO",
        )
        assert result.payment_method == "CRYPTO"
