from __future__ import annotations

import json

import pytest

from beexar import (
    ApiCode,
    BalanceResult,
    BetWinResult,
    BetWinTransactionResult,
    FinishResult,
    FreespinsFinishRequest,
    FreespinsFinishResult,
    Money,
    RollbackResult,
    WalletError,
    WalletServer,
    route_from_path,
    sign,
    verify,
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


@pytest.mark.parametrize("signature", ["é" * 64, "é"])
def test_verify_refuses_a_non_ascii_signature(signature: str) -> None:
    # compare_digest raises TypeError on a str with a non-ASCII character; a
    # header is not allowed to turn into an exception, only into False.
    assert verify(b"{}", signature, SECRET) is False


def test_non_ascii_signature_is_400_with_api_code_403() -> None:
    out = server().dispatch("/balance", b"{}", "é" * 64)
    assert out.status == 400
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


class FreespinsStubWallet(StubWallet):
    """StubWallet plus the optional fifth method."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: FreespinsFinishRequest = None  # type: ignore[assignment]

    def freespins_finish(self, request, context):  # type: ignore[no-untyped-def]
        self.seen = request
        return FreespinsFinishResult(balance=Money.parse("112.50"))


FREESPINS_FINISH = b'{"issue_id":"fs-1","amount":"12.50"}'


def test_freespins_finish_reaches_the_optional_handler() -> None:
    wallet = FreespinsStubWallet()
    srv = WalletServer(wallet, SECRET)

    out = srv.dispatch("/freespins/finish", FREESPINS_FINISH, signed(FREESPINS_FINISH))
    assert out.status == 200
    assert json.loads(out.body) == {"balance": "112.50"}
    assert wallet.seen.issue_id == "fs-1"
    assert str(wallet.seen.amount) == "12.50"

    # Zero is a result here, not a bad request: the freespins won nothing.
    zero = b'{"issue_id":"fs-1","amount":"0"}'
    assert srv.dispatch("/freespins/finish", zero, signed(zero)).status == 200
    assert wallet.seen.amount.is_zero()


@pytest.mark.parametrize(
    "body,message",
    [
        (b'{"issue_id":"fs-1","amount":"-1.00"}', "amount: not a valid decimal amount"),
        (b'{"issue_id":"fs-1","amount":12.5}', "amount: not a valid decimal amount"),
        (b'{"issue_id":"fs-1"}', "amount: not a valid decimal amount"),
        (b'{"amount":"12.50"}', "issue_id: required, must be a non-empty string"),
    ],
)
def test_freespins_finish_is_validated_before_the_handler(body: bytes, message: str) -> None:
    wallet = FreespinsStubWallet()
    out = WalletServer(wallet, SECRET).dispatch("/freespins/finish", body, signed(body))
    assert out.status == 400
    assert json.loads(out.body)["msg"] == message
    assert wallet.seen is None


def test_freespins_finish_without_the_handler_answers_like_an_unknown_route() -> None:
    # StubWallet has the four methods only — a wallet written before freespins
    # existed, or one that does not offer them. It must keep working, and answer
    # /freespins/finish the way an unknown route is.
    out = server().dispatch("/freespins/finish", FREESPINS_FINISH, signed(FREESPINS_FINISH))
    assert out.status == 400
    assert json.loads(out.body) == {
        "code": "invalid_argument",
        "msg": "freespins finish is not implemented by this wallet",
        "meta": {"api_code": ApiCode.NOT_FOUND, "api_message": "freespins finish is not implemented by this wallet"},
    }
    body = b'{"account_id":"p","currency":"EUR","game_id":"d"}'
    assert server().dispatch("/balance", body, signed(body)).status == 200


def test_freespins_refusals_carry_their_api_codes() -> None:
    assert WalletError.invalid_freespins_issue().to_envelope()["meta"]["api_code"] == "610"
    assert WalletError.freespins_state_transition().to_envelope()["meta"]["api_code"] == "605"


@pytest.mark.parametrize(
    "path,route",
    [
        ("/freespins/finish", "/freespins/finish"),
        ("/x/freespins/finish", "/freespins/finish"),
        ("/wallet/freespins/finish/", "/freespins/finish"),
        ("/finish", "/finish"),
        ("/x/finish", "/finish"),
    ],
)
def test_route_from_path_prefers_the_longest_route(path: str, route: str) -> None:
    # /freespins/finish ends with /finish too. Matching the shorter route first
    # would hand a freespins result to the round's finish handler.
    assert route_from_path(path) == route


def test_route_from_path_does_not_match_a_bare_prefix() -> None:
    assert route_from_path("/freespins") is None
