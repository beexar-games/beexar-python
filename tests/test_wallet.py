from __future__ import annotations

import json

import pytest

from beexar import (
    ApiCode,
    BalanceResult,
    BetWinResult,
    BetWinTransactionResult,
    FinishResult,
    Money,
    RollbackResult,
    WalletError,
    WalletServer,
    route_from_path,
    sign,
)

SECRET = "unit-test-secret"

# A body whose key order no JSON serialiser produces. If anything parses and
# re-serialises before the SDK sees it, these bytes change and the signature
# cannot match.
HOSTILE_BODY = (
    b'{"transactions":[{"type":"bet","id_provider":"tx-1","amount":"10.00"}],'
    b'"round_id":"r-1","game_id":"dice","currency":"EUR","account_id":"player_1"}'
)


class StubWallet:
    def __init__(self, bet_win_error: Exception = None) -> None:  # type: ignore[assignment]
        self._bet_win_error = bet_win_error

    def balance(self, request, context):  # type: ignore[no-untyped-def]
        return BalanceResult(balance=Money.parse("100.00"))

    def bet_win(self, request, context):  # type: ignore[no-untyped-def]
        if self._bet_win_error is not None:
            raise self._bet_win_error
        return BetWinResult(
            round_id="own-round",
            balance=Money.parse("90.00"),
            transactions=[BetWinTransactionResult(t.id_provider, "own-1") for t in request.transactions],
        )

    def rollback(self, request, context):  # type: ignore[no-untyped-def]
        return RollbackResult(balance=Money.parse("100.00"), round_id="own-round", transactions=[])

    def finish(self, request, context):  # type: ignore[no-untyped-def]
        return FinishResult(balance=Money.parse("100.00"))


def server(on_warning=None) -> WalletServer:  # type: ignore[no-untyped-def]
    return WalletServer(StubWallet(), SECRET, on_warning=on_warning)


def signed(body: bytes) -> str:
    return sign(body, SECRET)


@pytest.mark.parametrize("secret", ["", "   "])
def test_refuses_an_empty_api_secret(secret: str) -> None:
    # The gateway treats an empty secret as an automatic failure, so a server
    # built with one would authenticate nothing while looking healthy.
    with pytest.raises(ValueError):
        WalletServer(StubWallet(), secret)


def test_signature_failure_is_400_with_api_code_403() -> None:
    out = server().dispatch("/balance", b"{}", "0" * 64)
    assert out.status == 400, "the wallet contract never answers HTTP 403"
    assert json.loads(out.body)["meta"]["api_code"] == ApiCode.FORBIDDEN


def test_diagnostic_names_the_reserialisation() -> None:
    warnings: list = []
    srv = server(on_warning=warnings.append)

    # What a body parser would hand over: json.dumps of the parsed dict.
    reserialised = b'{"account_id":"p","currency":"EUR","game_id":"dice"}'
    srv.dispatch("/balance", reserialised, "0" * 64)
    assert "re-serialisation" in " ".join(warnings)

    warnings.clear()
    srv.dispatch("/balance", b"", "0" * 64)
    assert "consumed the stream" in " ".join(warnings)


def test_refuses_a_decoded_string_because_a_decode_is_not_always_reversible() -> None:
    with pytest.raises(TypeError):
        server().dispatch("/balance", "{}", "x")  # type: ignore[arg-type]


def test_refuses_an_oversize_body() -> None:
    huge = b"x" * (64 * 1024 + 1)
    out = server().dispatch("/balance", huge, signed(huge))
    assert out.status == 400
    assert json.loads(out.body)["meta"]["api_code"] == ApiCode.BAD_REQUEST


@pytest.mark.parametrize(
    "route,body,needle",
    [
        (
            "/betwin",
            b'{"account_id":"p","currency":"EUR","game_id":"d","round_id":"r",'
            b'"transactions":[{"id_provider":"t","type":"bet","amount":"0"}]}',
            "greater than zero",
        ),
        ("/balance", b'{"account_id":"p","currency":"eur","game_id":"d"}', "currency"),
        ("/balance", b"{", "malformed JSON"),
    ],
)
def test_validation_runs_before_the_handler(route: str, body: bytes, needle: str) -> None:
    out = server().dispatch(route, body, signed(body))
    assert out.status == 400
    assert needle in out.body.decode()


def test_unknown_fields_are_tolerated() -> None:
    # additionalProperties is true on purpose, and the gateway probes operators
    # with an extra field. Strictness here breaks a valid request.
    body = b'{"account_id":"p","currency":"EUR","game_id":"d","new_param":"probe"}'
    assert server().dispatch("/balance", body, signed(body)).status == 200


def test_unexpected_exception_leaks_nothing() -> None:
    srv = WalletServer(StubWallet(RuntimeError("connection to ledger-db-7 refused")), SECRET)
    out = srv.dispatch("/betwin", HOSTILE_BODY, signed(HOSTILE_BODY))
    assert out.status == 500
    assert b"ledger-db-7" not in out.body
    assert json.loads(out.body) == {
        "code": "internal",
        "msg": "internal error",
        "meta": {"api_code": "500", "api_message": "internal error"},
    }


def test_funds_errors_cannot_be_built_without_a_balance() -> None:
    with pytest.raises(TypeError):
        WalletError.with_api_code(ApiCode.INSUFFICIENT_FUNDS, "no funds")
    balance = Money.parse("1.23")
    for error in (
        WalletError.insufficient_funds(balance),
        WalletError.bet_limit_reached(balance),
        WalletError.max_bet_exceeded(balance),
    ):
        assert error.to_envelope()["meta"]["balance"] == "1.23"


def test_bonus_amount_defaults_to_zero() -> None:
    # The contract requires the field in every transaction.
    out = server().dispatch("/betwin", HOSTILE_BODY, signed(HOSTILE_BODY))
    assert json.loads(out.body)["transactions"][0]["bonus_amount"] == "0.00"


@pytest.mark.parametrize("path", ["/betwin", "/wallet/betwin", "/api/v2/psp/betwin", "/wallet/betwin/"])
def test_route_from_path_accepts_any_mount_prefix(path: str) -> None:
    # The gateway POSTs to the full callback URL from the backoffice.
    assert route_from_path(path) == "/betwin"


def test_route_from_path_ignores_everything_else() -> None:
    assert route_from_path("/health") is None
