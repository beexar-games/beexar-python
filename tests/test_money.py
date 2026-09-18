from __future__ import annotations

import json
import time

import pytest

from beexar import MAX_CLIENT_DECIMAL_LEN, Money, MoneyError, is_valid_currency


def test_keeps_the_scale_it_was_given() -> None:
    for value in ("0.90", "100", "1.2300"):
        assert str(Money.parse(value)) == value


def test_rejects_the_exponent_bomb_before_any_arithmetic() -> None:
    # The whole point: this is built in microseconds by every bignum library and
    # then asks for a multi-gigabyte integer on the first rescale.
    started = time.monotonic()
    with pytest.raises(MoneyError):
        Money.parse("1E2000000000")
    assert time.monotonic() - started < 0.05


@pytest.mark.parametrize(
    "value",
    ["", "1.12345678901234567", "-1.00", "+1.00", " 1.00", "1,00", "NaN", "Infinity", ".5", "1.",
     "1" * (MAX_CLIENT_DECIMAL_LEN + 1)],
)
def test_rejects_what_the_contract_rejects(value: str) -> None:
    assert Money.try_parse(value) is None


@pytest.mark.parametrize("value", [1.1, 110, True, None, b"1.00"])
def test_rejects_a_non_string_so_a_float_can_never_enter(value: object) -> None:
    assert Money.try_parse(value) is None


def test_arithmetic_aligns_scales_without_losing_digits() -> None:
    assert str(Money.parse("0.1").add(Money.parse("0.2"))) == "0.3"
    assert str(Money.parse("100.00").sub(Money.parse("0.005"))) == "99.995"


def test_subtraction_can_go_negative_so_a_bet_can_be_refused() -> None:
    after = Money.parse("10.00").sub(Money.parse("25.00"))
    assert after.is_negative()
    assert str(after.clamp_to_zero()) == "0.00"


def test_compares_by_value_not_by_string() -> None:
    assert Money.parse("1.5").compare(Money.parse("1.50")) == 0
    assert Money.parse("1.5") == Money.parse("1.50")
    assert Money.parse("2").compare(Money.parse("10")) == -1


def test_survives_values_past_float_precision() -> None:
    huge = Money.parse("90071992547409910.99")
    assert str(huge.add(Money.parse("0.01"))) == "90071992547409911.00"


def test_format_truncates_toward_zero() -> None:
    # Rounding up here would inflate what the operator owes.
    assert Money.parse("1.999").format(2) == "1.99"
    assert Money.parse("1.001").format(2) == "1.00"
    assert Money.parse("1").format(4) == "1.0000"
    assert Money.parse("1.5").format(0) == "1"


def test_format_bonus_clamps_to_the_twelve_digits_the_contract_allows() -> None:
    assert Money.parse("1.1234567890123456").format_bonus() == "1.123456789012"
    assert Money.zero().format_bonus() == "0.00"


def test_refuses_to_be_coerced_or_added_with_an_operator() -> None:
    money = Money.parse("1.00")
    with pytest.raises(MoneyError):
        float(money)
    with pytest.raises(MoneyError):
        money + money  # noqa: B015 — the point is that it raises
    with pytest.raises(MoneyError):
        money - money  # noqa: B015


def test_from_minor_units_bridges_an_integer_ledger() -> None:
    assert str(Money.from_minor_units(9970, 2)) == "99.70"
    assert Money.from_minor_units(9970, 2).minor_units() == 9970
    with pytest.raises(MoneyError):
        Money.from_minor_units(1, 17)
    with pytest.raises(MoneyError):
        Money.from_minor_units(1.5, 2)  # type: ignore[arg-type]


def test_serialises_to_its_wire_form() -> None:
    assert json.dumps({"balance": str(Money.parse("12.30"))}) == '{"balance": "12.30"}'


@pytest.mark.parametrize("code", ["EUR", "USD", "BTC", "CUSTOM_WIN", "XBT1"])
def test_accepts_valid_currencies(code: str) -> None:
    assert is_valid_currency(code)


@pytest.mark.parametrize("code", ["eur", "Eur", "EU", "1EUR", "_EUR", "", None])
def test_rejects_invalid_currencies_without_normalising(code: object) -> None:
    assert not is_valid_currency(code)
