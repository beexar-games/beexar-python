"""The wallet server: one pure function behind the four callbacks."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional

from .errors import WalletError
from .money import Money, is_valid_currency, CURRENCY_PATTERN
from .signature import verify
from .types import (
    ROUTES,
    BalanceRequest,
    BetWinRequest,
    BetWinTransaction,
    FinishRequest,
    RequestContext,
    RollbackRequest,
    RollbackTransaction,
    Route,
)

__all__ = ["WalletServer", "DispatchResponse", "MAX_BODY_BYTES", "route_from_path"]

#: The gateway caps an operator request body at 64 KiB. The wallet server
#: mirrors that so a body it would never have sent cannot make your process
#: allocate.
MAX_BODY_BYTES = 64 * 1024


class DispatchResponse(NamedTuple):
    status: int
    body: bytes
    headers: Dict[str, str]


def route_from_path(path: str) -> Optional[Route]:
    """Map a request path to one of the four callback routes.

    Matching is on the SUFFIX because the mount path belongs to you: the gateway
    POSTs to the full callback URL configured in the backoffice, prefix and all,
    so ``/api/v2/psp/betwin`` is as valid as ``/betwin``.
    """
    clean = path.split("?", 1)[0].rstrip("/") or "/"
    for route in ROUTES:
        if clean == route or clean.endswith(route):
            return route
    return None


class WalletServer:
    """Verifies, parses, validates and routes one callback.

    :meth:`dispatch` takes bytes and a signature and returns a status and a
    body. No framework type crosses that boundary and it never sees a parsed
    body from the outside, so signing the wrong thing is not expressible.
    """

    def __init__(
        self,
        handler: Any,
        api_secret: str,
        max_body_bytes: int = MAX_BODY_BYTES,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not isinstance(api_secret, str) or not api_secret.strip():
            # The gateway treats an empty secret as an automatic verification
            # failure, so a server built with one would authenticate nothing
            # while looking healthy.
            raise ValueError("WalletServer: api_secret is required and must be non-empty")
        self._handler = handler
        self._api_secret = api_secret
        self._max_body_bytes = max_body_bytes
        self._warn = on_warning or (lambda message: None)

    def dispatch(
        self,
        route: Route,
        raw_body: bytes,
        signature: Optional[str],
        headers: Optional[Mapping[str, str]] = None,
    ) -> DispatchResponse:
        try:
            return self._dispatch(route, raw_body, signature, dict(headers or {}))
        except WalletError as error:
            return _error_response(error)
        except TypeError:
            # A programming error in the calling code — a funds error built
            # without a balance. Surface it; swallowing it would hide a bug that
            # only ever produces wrong money.
            raise
        except Exception:  # noqa: BLE001 — deliberate: nothing about it leaves this process
            # Anything the handler raised: answer 500 and say nothing about it.
            # The platform retries 5xx with the same id_provider, so this is the
            # retryable shape.
            return _error_response(WalletError.internal())

    def _dispatch(
        self, route: Route, raw_body: bytes, signature: Optional[str], headers: Dict[str, str]
    ) -> DispatchResponse:
        if route not in ROUTES:
            raise WalletError.not_found("unknown route: {}".format(route))
        if not isinstance(raw_body, (bytes, bytearray)):
            # A str here means something already decoded the bytes, and a decode
            # is not always reversible. Refuse rather than verify a lossy copy.
            raise TypeError("WalletServer.dispatch: raw_body must be the bytes as received")
        if len(raw_body) > self._max_body_bytes:
            raise WalletError.bad_request("request body too large")
        if not verify(raw_body, signature, self._api_secret):
            self._explain_signature_failure(bytes(raw_body), signature)
            raise WalletError.invalid_signature()

        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise WalletError.bad_request("malformed JSON body")
        if not isinstance(parsed, dict):
            raise WalletError.bad_request("body must be a JSON object")

        context = RequestContext(route=route, raw_body=bytes(raw_body), headers=headers)

        if route == "/balance":
            result = self._handler.balance(_parse_balance(parsed), context)
            return _ok({"balance": str(result.balance)})

        if route == "/betwin":
            result = self._handler.bet_win(_parse_bet_win(parsed), context)
            return _ok(
                {
                    "round_id": result.round_id,
                    "transactions": [
                        {
                            "id_provider": t.id_provider,
                            "id": t.id,
                            "bonus_amount": (t.bonus_amount or Money.zero()).format_bonus(),
                        }
                        for t in result.transactions
                    ],
                    "balance": str(result.balance),
                }
            )

        if route == "/rollback":
            result = self._handler.rollback(_parse_rollback(parsed), context)
            return _ok(
                {
                    "balance": str(result.balance),
                    "round_id": result.round_id,
                    "transactions": [
                        {"id_provider": t.id_provider, "id": t.id} for t in result.transactions
                    ],
                }
            )

        result = self._handler.finish(_parse_finish(parsed), context)
        return _ok({"balance": str(result.balance)})

    def _explain_signature_failure(self, raw_body: bytes, signature: Optional[str]) -> None:
        """Say why a signature failed when the SDK can tell.

        Nearly every failed integration is the same bug: a body-parsing layer
        consumed the stream, and what reaches the SDK is a re-serialisation
        rather than the bytes that were signed. That is unrecoverable, but it is
        recognisable, and naming it saves days.
        """
        if not signature:
            self._warn("X-REQUEST-SIGN header is missing from the request")
            return
        if not raw_body:
            self._warn(
                "the request body reaching the SDK is empty, which usually means something "
                "already consumed the stream — read the body once and pass the same bytes to dispatch()"
            )
            return
        try:
            text = raw_body.decode("utf-8")
            reserialised = json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
        except (ValueError, UnicodeDecodeError):
            reserialised = None
        if reserialised is not None and reserialised == text:
            self._warn(
                "the body reaching the SDK is byte-identical to a JSON re-serialisation. If your "
                "framework parsed the body before the SDK saw it, the bytes that were signed are "
                "already lost and the signature can never match."
            )
            return
        self._warn("signature mismatch: check that the API secret matches the one in the backoffice")


# --- parsing and validation, all of it before the handler runs -------------


def _require_string(body: Mapping[str, Any], field: str, label: Optional[str] = None) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise WalletError.bad_request("{}: required, must be a non-empty string".format(label or field))
    return value


def _optional_string(body: Mapping[str, Any], field: str) -> Optional[str]:
    value = body.get(field)
    return value if isinstance(value, str) and value else None


def _require_currency(body: Mapping[str, Any]) -> str:
    value = body.get("currency")
    if not is_valid_currency(value):
        raise WalletError.bad_request("currency: must match {}".format(CURRENCY_PATTERN))
    assert isinstance(value, str)
    return value


def _require_array(body: Mapping[str, Any], field: str) -> List[Any]:
    value = body.get(field)
    if not isinstance(value, list) or not value:
        raise WalletError.bad_request("{}: required, must be a non-empty array".format(field))
    return value


def _require_amount(raw: Any, path: str) -> Money:
    amount = Money.try_parse(raw)
    if amount is None:
        raise WalletError.bad_request("{}: not a valid decimal amount".format(path))
    if not amount.is_positive():
        raise WalletError.bad_request("{}: must be greater than zero".format(path))
    return amount


def _parse_balance(body: Mapping[str, Any]) -> BalanceRequest:
    return BalanceRequest(
        account_id=_require_string(body, "account_id"),
        currency=_require_currency(body),
        game_id=_require_string(body, "game_id"),
        session_id=_optional_string(body, "session_id"),
    )


def _parse_bet_win(body: Mapping[str, Any]) -> BetWinRequest:
    transactions = []
    for i, raw in enumerate(_require_array(body, "transactions")):
        if not isinstance(raw, dict):
            raise WalletError.bad_request("transactions[{}]: must be an object".format(i))
        kind = _require_string(raw, "type", "transactions[{}].type".format(i))
        if kind not in ("bet", "win"):
            raise WalletError.bad_request('transactions[{}].type: must be "bet" or "win"'.format(i))
        transactions.append(
            BetWinTransaction(
                id_provider=_require_string(raw, "id_provider", "transactions[{}].id_provider".format(i)),
                type=kind,
                amount=_require_amount(raw.get("amount"), "transactions[{}].amount".format(i)),
            )
        )

    return BetWinRequest(
        account_id=_require_string(body, "account_id"),
        currency=_require_currency(body),
        game_id=_require_string(body, "game_id"),
        round_id=_require_string(body, "round_id"),
        finished=bool(body.get("finished", False)),
        transactions=transactions,
        session_id=_optional_string(body, "session_id"),
    )


def _parse_rollback(body: Mapping[str, Any]) -> RollbackRequest:
    transactions = []
    for i, raw in enumerate(_require_array(body, "transactions")):
        if not isinstance(raw, dict):
            raise WalletError.bad_request("transactions[{}]: must be an object".format(i))
        kind = _require_string(raw, "type", "transactions[{}].type".format(i))
        if kind != "rollback":
            raise WalletError.bad_request('transactions[{}].type: must be "rollback"'.format(i))
        transactions.append(
            RollbackTransaction(
                id_provider=_require_string(raw, "id_provider", "transactions[{}].id_provider".format(i)),
                original_id_provider=_require_string(
                    raw, "original_id_provider", "transactions[{}].original_id_provider".format(i)
                ),
            )
        )

    return RollbackRequest(
        account_id=_require_string(body, "account_id"),
        currency=_require_currency(body),
        # Liberal: the contract requires game_id and the platform always sends
        # it, but refusing a request we could serve helps nobody.
        game_id=_optional_string(body, "game_id") or "",
        round_id_provider=_require_string(body, "round_id_provider"),
        finished=bool(body.get("finished", False)),
        transactions=transactions,
        session_id=_optional_string(body, "session_id"),
    )


def _parse_finish(body: Mapping[str, Any]) -> FinishRequest:
    return FinishRequest(
        account_id=_require_string(body, "account_id"),
        currency=_require_currency(body),
        round_id=_require_string(body, "round_id"),
        session_id=_optional_string(body, "session_id"),
    )


def _ok(body: Dict[str, Any]) -> DispatchResponse:
    return DispatchResponse(
        status=200,
        body=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def _error_response(error: WalletError) -> DispatchResponse:
    return DispatchResponse(
        status=error.status,
        body=json.dumps(error.to_envelope(), separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
