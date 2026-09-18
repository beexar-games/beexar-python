"""The request and result shapes your handler sees.

Wire field names are kept verbatim — ``account_id``, ``id_provider``,
``round_id_provider``, ``bonus_amount``. What you read in the docs, see in a
request log and type in your handler is then the same string, and the SDK
carries no name-mapping table that could drift from the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .money import Money

__all__ = [
    "Route",
    "ROUTES",
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
    "WalletHandler",
]

#: The four callback paths, exactly as the contract names them.
Route = str
ROUTES = ("/balance", "/betwin", "/rollback", "/finish")


@dataclass(frozen=True)
class RequestContext:
    """Everything about the request that is not part of the parsed body."""

    route: Route
    #: The exact bytes the signature was verified against.
    raw_body: bytes
    #: Lower-cased header names.
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BalanceRequest:
    account_id: str
    currency: str
    game_id: str
    session_id: Optional[str] = None


@dataclass(frozen=True)
class BalanceResult:
    balance: Money


@dataclass(frozen=True)
class BetWinTransaction:
    id_provider: str
    #: Either ``"bet"`` or ``"win"``.
    type: str
    #: Always greater than zero — the SDK rejects 0 before your code runs.
    amount: Money


@dataclass(frozen=True)
class BetWinRequest:
    account_id: str
    currency: str
    game_id: str
    round_id: str
    finished: bool
    #: In request order. Apply them in this order, atomically.
    transactions: List[BetWinTransaction]
    session_id: Optional[str] = None


@dataclass(frozen=True)
class BetWinTransactionResult:
    #: Echo the platform's id back unchanged.
    id_provider: str
    #: YOUR transaction id. On a replay, return the one you stored the first time.
    id: str
    #: Bonus balance moved by this transaction. The contract requires the field;
    #: the SDK emits ``"0.00"`` when you leave it None.
    bonus_amount: Optional[Money] = None


@dataclass(frozen=True)
class BetWinResult:
    #: YOUR round id.
    round_id: str
    #: Balance after all transactions in this request.
    balance: Money
    #: One entry per request transaction, in the same order.
    transactions: List[BetWinTransactionResult]


@dataclass(frozen=True)
class RollbackTransaction:
    id_provider: str
    #: The ``id_provider`` of the transaction being reversed.
    original_id_provider: str


@dataclass(frozen=True)
class RollbackRequest:
    account_id: str
    currency: str
    #: Required by the contract and always sent by the platform, but the SDK
    #: accepts its absence and gives you ``""`` rather than rejecting a request
    #: it could have served. Liberal in, strict out.
    game_id: str
    #: Note the name: rollback uses ``round_id_provider``, betwin and finish use
    #: ``round_id``.
    round_id_provider: str
    finished: bool
    transactions: List[RollbackTransaction]
    session_id: Optional[str] = None


@dataclass(frozen=True)
class RollbackTransactionResult:
    id_provider: str
    #: Your transaction id, or ``""`` when there was nothing to reverse.
    id: str


@dataclass(frozen=True)
class RollbackResult:
    balance: Money
    round_id: str
    transactions: List[RollbackTransactionResult]


@dataclass(frozen=True)
class FinishRequest:
    account_id: str
    currency: str
    round_id: str
    #: ``/finish`` carries no ``game_id``.
    session_id: Optional[str] = None


@dataclass(frozen=True)
class FinishResult:
    balance: Money


class WalletHandler:
    """The four methods you implement against your own ledger.

    Everything else — signature verification, parsing, validation, the Twirp
    error envelope — is the SDK's job. Raise a
    :class:`~beexar.errors.WalletError` to answer with a specific api_code;
    anything else you raise becomes an opaque 500.

    Subclass it, or just pass any object with these four methods — the server
    only ever calls them by name.
    """

    def balance(self, request: BalanceRequest, context: RequestContext) -> BalanceResult:
        raise NotImplementedError

    def bet_win(self, request: BetWinRequest, context: RequestContext) -> BetWinResult:
        raise NotImplementedError

    def rollback(self, request: RollbackRequest, context: RequestContext) -> RollbackResult:
        raise NotImplementedError

    def finish(self, request: FinishRequest, context: RequestContext) -> FinishResult:
        raise NotImplementedError
