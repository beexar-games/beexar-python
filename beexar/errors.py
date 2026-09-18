"""The api_code registry and the two error types this contract uses."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .money import Money

__all__ = ["ApiCode", "TwirpCode", "is_funds_related_code", "WalletError", "ApiError"]


class ApiCode:
    """SoftSwiss API codes, ported verbatim from the platform's own registry.

    Two things about this contract surprise everyone once:

    1. There are only two HTTP statuses (400 and 500) and two ``code`` values
       (``invalid_argument`` and ``internal``). All meaning lives in
       ``meta.api_code``. Branch on that, never on the status.
    2. A signature failure is HTTP 400 with api_code ``403``, not HTTP 403.
       Answering 403 breaks the contract.
    """

    INSUFFICIENT_FUNDS = "100"
    INVALID_PLAYER = "101"
    BET_LIMIT_REACHED = "105"
    MAX_BET_EXCEEDED = "106"
    GAME_FORBIDDEN = "107"
    PLAYER_DISABLED = "110"
    COUNTRY_RESTRICTED = "153"
    CURRENCY_NOT_ALLOWED = "154"
    FIELD_IMMUTABLE = "155"

    BAD_REQUEST = "400"
    FORBIDDEN = "403"
    NOT_FOUND = "404"
    #: Beexar extension. Return it from /betwin to reject a transaction whose
    #: id_provider was already rolled back — including a rollback that arrived
    #: BEFORE the bet it reverses.
    ALREADY_ROLLED_BACK = "409"

    GAME_NOT_AVAILABLE = "405"
    CASINO_DISABLED = "410"

    UNKNOWN_ERROR = "500"
    SERVICE_UNAVAILABLE = "503"
    REQUEST_TIMEOUT = "504"


class TwirpCode:
    """The only two ``code`` values this contract uses."""

    INVALID_ARGUMENT = "invalid_argument"
    INTERNAL = "internal"


def is_funds_related_code(code: str) -> bool:
    """Whether an api_code must carry the player's balance in ``meta.balance``.

    The platform will not complain if you omit it — it reads the field with the
    error swallowed — so the player simply sees a wrong balance. That is exactly
    why this SDK refuses to build such an error without one.
    """
    return code in (ApiCode.INSUFFICIENT_FUNDS, ApiCode.BET_LIMIT_REACHED, ApiCode.MAX_BET_EXCEEDED)


class WalletError(Exception):
    """Raise this from your handler to answer with a specific api_code.

    Anything else you raise becomes an opaque 500 — your message is never echoed
    to the platform.
    """

    def __init__(
        self,
        status: int,
        twirp_code: str,
        api_code: str,
        message: str,
        balance: Optional[Money] = None,
    ) -> None:
        super().__init__(message)
        if is_funds_related_code(api_code) and balance is None:
            raise TypeError(
                "api_code {} must carry the player's balance; use insufficient_funds(), "
                "bet_limit_reached() or max_bet_exceeded()".format(api_code)
            )
        self.status = status
        self.twirp_code = twirp_code
        self.api_code = api_code
        self.message = message
        self.balance = balance

    # --- funds-related: the balance is a required argument, by design ------

    @classmethod
    def insufficient_funds(cls, balance: Money, message: str = "insufficient funds") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.INSUFFICIENT_FUNDS, message, balance)

    @classmethod
    def bet_limit_reached(cls, balance: Money, message: str = "bet limit reached") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.BET_LIMIT_REACHED, message, balance)

    @classmethod
    def max_bet_exceeded(cls, balance: Money, message: str = "max bet exceeded") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.MAX_BET_EXCEEDED, message, balance)

    # --- player / request --------------------------------------------------

    @classmethod
    def invalid_player(cls, message: str = "invalid player") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.INVALID_PLAYER, message)

    @classmethod
    def player_disabled(cls, message: str = "player is disabled") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.PLAYER_DISABLED, message)

    @classmethod
    def game_forbidden(cls, message: str = "game is forbidden to the player") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.GAME_FORBIDDEN, message)

    @classmethod
    def currency_not_allowed(cls, message: str = "currency is not allowed for the player") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.CURRENCY_NOT_ALLOWED, message)

    @classmethod
    def bad_request(cls, message: str = "bad request") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.BAD_REQUEST, message)

    @classmethod
    def invalid_signature(cls, message: str = "invalid signature") -> "WalletError":
        """HTTP 400 with api_code 403 — the wallet contract never answers HTTP 403."""
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.FORBIDDEN, message)

    @classmethod
    def not_found(cls, message: str = "not found") -> "WalletError":
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.NOT_FOUND, message)

    @classmethod
    def already_rolled_back(cls, message: str = "action already rolled back") -> "WalletError":
        """The transaction's ``id_provider`` was already rolled back (tombstoned)."""
        return cls(400, TwirpCode.INVALID_ARGUMENT, ApiCode.ALREADY_ROLLED_BACK, message)

    # --- server ------------------------------------------------------------

    @classmethod
    def internal(cls, message: str = "internal error") -> "WalletError":
        return cls(500, TwirpCode.INTERNAL, ApiCode.UNKNOWN_ERROR, message)

    @classmethod
    def service_unavailable(cls, message: str = "service unavailable") -> "WalletError":
        return cls(500, TwirpCode.INTERNAL, ApiCode.SERVICE_UNAVAILABLE, message)

    @classmethod
    def with_api_code(
        cls, api_code: str, message: str, balance: Optional[Money] = None
    ) -> "WalletError":
        """Escape hatch for an api_code without a dedicated constructor.

        It runs the same balance check, so it cannot be used to bypass it.
        """
        status = 500 if api_code.startswith("5") else 400
        twirp = TwirpCode.INTERNAL if status >= 500 else TwirpCode.INVALID_ARGUMENT
        return cls(status, twirp, api_code, message, balance)

    def to_envelope(self) -> Dict[str, Any]:
        meta: Dict[str, str] = {"api_code": self.api_code, "api_message": self.message}
        if self.balance is not None:
            meta["balance"] = str(self.balance)
        return {"code": self.twirp_code, "msg": self.message, "meta": meta}


class ApiError(Exception):
    """An error answer from the Beexar gateway to one of YOUR calls."""

    def __init__(self, http_status: int, code: str, api_code: str, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.api_code = api_code
        self.api_message = message

    @classmethod
    def from_envelope(cls, status: int, envelope: Dict[str, Any]) -> "ApiError":
        meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
        message = meta.get("api_message") or envelope.get("msg") or "unknown error"
        return cls(
            status,
            str(envelope.get("code", TwirpCode.INTERNAL)),
            str(meta.get("api_code", status)),
            str(message),
        )

    def is_retryable(self) -> bool:
        """5xx only. A 400 on this contract is terminal."""
        return self.http_status >= 500

    def is_signature_error(self) -> bool:
        """Keyed on api_code, because the status alone is ambiguous."""
        return self.api_code == ApiCode.FORBIDDEN
