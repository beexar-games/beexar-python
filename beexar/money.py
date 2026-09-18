"""Decimal money that can never become a float."""

from __future__ import annotations

import re
from decimal import Decimal, localcontext
from typing import Any, Optional

__all__ = ["Money", "MoneyError", "MAX_SCALE", "MAX_CLIENT_DECIMAL_LEN", "is_valid_currency", "CURRENCY_PATTERN"]


class MoneyError(ValueError):
    """Raised when a string is not a valid amount on this contract."""


#: Upper bound on fractional digits, mirroring the platform's NUMERIC(38,16).
MAX_SCALE = 16

#: Length cap on an untrusted decimal string, mirroring ``maxLength: 40``.
MAX_CLIENT_DECIMAL_LEN = 40

# The shape the contract accepts: a non-negative plain decimal, at most
# MAX_SCALE fractional digits, and NO scientific notation.
#
# Rejecting the exponent form is the load-bearing part, not a style choice.
# Decimal("1E2000000000") is built in microseconds and costs nothing until the
# first rescale, at which point it asks for a multi-gigabyte integer and takes
# the process with it. The guard has to run on shape, before any arithmetic.
_CLIENT_DECIMAL = re.compile(r"^\d+(\.\d{1,16})?$")

CURRENCY_PATTERN = r"^[A-Z][A-Z0-9_]{2,31}$"
_CURRENCY = re.compile(CURRENCY_PATTERN)

# Wide enough that adding two 38-digit balances never rounds. The platform
# stores NUMERIC(38,16); 80 digits leaves room and costs nothing.
_PRECISION = 80


class Money:
    """A decimal amount in a currency's main unit — ``"0.90"`` is ninety cents.

    There is deliberately no constructor taking a ``float``. IEEE-754 cannot
    hold 0.1, and a wallet that rounds a player's balance by a hundredth of a
    cent per round is a reconciliation incident, not a rounding detail.
    """

    __slots__ = ("_value",)

    def __init__(self, value: Decimal) -> None:
        # Private by convention: build one with parse() or from_minor_units().
        self._value = value

    # --- construction ------------------------------------------------------

    @classmethod
    def parse(cls, value: str) -> "Money":
        """Read a decimal string from the wire, raising on anything else."""
        parsed = cls.try_parse(value)
        if parsed is None:
            raise MoneyError("not a valid decimal amount: {!r}".format(value))
        return parsed

    @classmethod
    def try_parse(cls, value: Any) -> Optional["Money"]:
        """Like :meth:`parse` but returns ``None`` instead of raising."""
        # bool is an int subclass; neither is a decimal string.
        if not isinstance(value, str):
            return None
        if not value or len(value) > MAX_CLIENT_DECIMAL_LEN:
            return None
        if _CLIENT_DECIMAL.match(value) is None:
            return None
        return cls(Decimal(value))

    @classmethod
    def zero(cls) -> "Money":
        """Zero at scale 2 — the scale the contract's examples use."""
        return cls(Decimal("0.00"))

    @classmethod
    def from_minor_units(cls, units: int, scale: int) -> "Money":
        """Build from an integer number of minor units, e.g. cents.

        ``Money.from_minor_units(9970, 2)`` is ``"99.70"``. This is the bridge
        for a ledger that stores integers.
        """
        if not isinstance(units, int) or isinstance(units, bool):
            raise MoneyError("minor units must be an int")
        if not isinstance(scale, int) or scale < 0 or scale > MAX_SCALE:
            raise MoneyError("scale must be an int in [0, {}], got {!r}".format(MAX_SCALE, scale))
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            return cls(Decimal(units).scaleb(-scale))

    # --- rendering ---------------------------------------------------------

    @property
    def scale(self) -> int:
        """Number of fractional digits this amount is expressed in."""
        exponent = self._value.as_tuple().exponent
        return -int(exponent) if isinstance(exponent, int) and exponent < 0 else 0

    def minor_units(self) -> int:
        """The unscaled integer value at :attr:`scale`."""
        return int(self._value.scaleb(self.scale))

    def format(self, scale: int) -> str:
        """Render with exactly ``scale`` fractional digits, truncating toward zero.

        Truncation matches the direction the platform truncates, so a rounding
        step can never inflate what the operator owes.
        """
        if not isinstance(scale, int) or scale < 0 or scale > MAX_SCALE:
            raise MoneyError("scale must be an int in [0, {}], got {!r}".format(MAX_SCALE, scale))
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            quantum = Decimal(1).scaleb(-scale)
            truncated = self._value.quantize(quantum, rounding="ROUND_DOWN")
        return "{:f}".format(truncated)

    def format_bonus(self) -> str:
        """Render ``bonus_amount``, which the contract caps at 12 fractional digits."""
        return self.format(min(self.scale, 12))

    def __str__(self) -> str:
        return "{:f}".format(self._value)

    def __repr__(self) -> str:
        return "Money({!r})".format(str(self))

    # --- arithmetic --------------------------------------------------------

    def add(self, other: "Money") -> "Money":
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            return Money(self._value + other._value)

    def sub(self, other: "Money") -> "Money":
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            return Money(self._value - other._value)

    def compare(self, other: "Money") -> int:
        """-1, 0 or 1."""
        if self._value < other._value:
            return -1
        if self._value > other._value:
            return 1
        return 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def is_zero(self) -> bool:
        return self._value == 0

    def is_negative(self) -> bool:
        return self._value < 0

    def is_positive(self) -> bool:
        return self._value > 0

    def clamp_to_zero(self) -> "Money":
        """Return this, or zero at the same scale when this is negative."""
        if not self.is_negative():
            return self
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            return Money(Decimal(0).quantize(Decimal(1).scaleb(-self.scale)))

    # --- guards ------------------------------------------------------------

    def __float__(self) -> float:
        raise MoneyError("Money has no float value — use str(), .add() or .sub()")

    def __add__(self, other: object) -> "Money":
        raise MoneyError("use .add(); `+` on money invites a float in")

    def __sub__(self, other: object) -> "Money":
        raise MoneyError("use .sub(); `-` on money invites a float in")


def is_valid_currency(code: Any) -> bool:
    """Whether ``code`` matches the contract's currency pattern.

    Lowercase is REJECTED, never normalised — the platform's own schema does the
    same, and quietly upcasing here would hide an operator bug until settlement.
    """
    return isinstance(code, str) and _CURRENCY.match(code) is not None
