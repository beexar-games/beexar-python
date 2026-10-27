"""Beexar operator SDK.

Two halves:

* :class:`~beexar.launcher.Client` — the calls you send to Beexar.
* :class:`~beexar.wallet.WalletServer` — the callbacks Beexar sends you.

See https://docs.beexar.com for the full contract.
"""

from __future__ import annotations

from .errors import ApiCode, ApiError, TwirpCode, WalletError, is_funds_related_code
from .launcher import PRODUCTION_BASE_URL, Client
from .money import (
    MAX_CLIENT_DECIMAL_LEN,
    MAX_SCALE,
    CURRENCY_PATTERN,
    Money,
    MoneyError,
    is_valid_currency,
)
from .signature import SIGNATURE_HEADER, sign, verify
from .types import (
    ROUTES,
    BalanceRequest,
    BalanceResult,
    BetWinRequest,
    BetWinResult,
    BetWinTransaction,
    BetWinTransactionResult,
    FinishRequest,
    FinishResult,
    FreespinsFinishRequest,
    FreespinsFinishResult,
    RequestContext,
    RollbackRequest,
    RollbackResult,
    RollbackTransaction,
    RollbackTransactionResult,
    Route,
    WalletHandler,
)
from .wallet import MAX_BODY_BYTES, DispatchResponse, WalletServer, route_from_path

#: Stamped at release time from sdk/VERSION. The same number is published for
#: the Node, PHP, Go and Python SDKs and always means the same contract snapshot.
__version__ = "1.2.0"

__all__ = [
    "__version__",
    "ApiCode",
    "ApiError",
    "TwirpCode",
    "WalletError",
    "is_funds_related_code",
    "Client",
    "PRODUCTION_BASE_URL",
    "Money",
    "MoneyError",
    "MAX_SCALE",
    "MAX_CLIENT_DECIMAL_LEN",
    "CURRENCY_PATTERN",
    "is_valid_currency",
    "sign",
    "verify",
    "SIGNATURE_HEADER",
    "ROUTES",
    "Route",
    "RequestContext",
    "BalanceRequest",
    "BalanceResult",
    "BetWinTransaction",
    "BetWinRequest",
    "BetWinTransactionResult",
    "BetWinResult",
    "RollbackTransaction",
    "RollbackRequest",
    "RollbackTransactionResult",
    "RollbackResult",
    "FinishRequest",
    "FinishResult",
    "FreespinsFinishRequest",
    "FreespinsFinishResult",
    "WalletHandler",
    "WalletServer",
    "DispatchResponse",
    "MAX_BODY_BYTES",
    "route_from_path",
]
