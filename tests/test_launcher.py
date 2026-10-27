from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from beexar import ApiCode, ApiError, Client, verify

SECRET = "launcher-test-secret"

ISSUE: Dict[str, Any] = {
    "issue_id": "fs-1",
    "games": ["dice", "plinko"],
    "freespins_quantity": 10,
    "bet_level": 2,
    "valid_until": "2026-11-30T12:00:00Z",
    "account": {"id": "player_1", "currency": "EUR"},
}


class Recorder:
    """A transport that answers with one status and body, and records what went on the wire."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: List[Tuple[str, str, Dict[str, str], Optional[bytes]]] = []

    def __call__(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> Tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        return self.status, self.body


def client(transport: Recorder) -> Client:
    return Client("casinoxyz", SECRET, base_url="https://gw.test", transport=transport)


def test_issue_freespins_signs_the_exact_bytes_it_sends() -> None:
    transport = Recorder(200, b"{}")
    assert client(transport).issue_freespins(ISSUE) is None

    method, url, headers, body = transport.calls[0]
    assert (method, url) == ("POST", "https://gw.test/api/v1/softswiss/freespins/issue")
    assert body is not None
    # The signature and the body come from one encoding, so they cannot drift —
    # this is the property that keeps the call accepted.
    assert verify(body, headers["X-REQUEST-SIGN"], SECRET)
    # Integers stay integers on the wire; the platform's schema refuses "10".
    assert json.loads(body) == {**ISSUE, "casino_id": "casinoxyz"}


def test_cancel_freespins_posts_the_issue_id() -> None:
    transport = Recorder(200, b"{}")
    assert client(transport).cancel_freespins({"issue_id": "fs-1"}) is None

    _, url, headers, body = transport.calls[0]
    assert url == "https://gw.test/api/v1/softswiss/freespins/cancel"
    assert body is not None
    assert json.loads(body) == {"issue_id": "fs-1", "casino_id": "casinoxyz"}
    assert verify(body, headers["X-REQUEST-SIGN"], SECRET)


def test_bet_levels_sends_the_filters_and_unwraps_the_answer() -> None:
    levels = [
        {
            "games": ["dice"],
            "currencies": [
                {
                    "currency": "EUR",
                    "bet_levels": [{"bet_level": 1, "amount": "0.10"}, {"bet_level": 2, "amount": "0.20"}],
                }
            ],
        }
    ]
    transport = Recorder(200, json.dumps({"bet_levels": levels}).encode())

    assert client(transport).bet_levels(games=["dice"], currencies=["EUR"]) == levels
    _, url, _, body = transport.calls[0]
    assert url == "https://gw.test/api/v1/softswiss/bet/levels"
    assert body is not None
    assert json.loads(body) == {"games": ["dice"], "currencies": ["EUR"], "casino_id": "casinoxyz"}


def test_bet_levels_without_filters_asks_for_everything() -> None:
    transport = Recorder(200, b'{"bet_levels":[]}')
    assert client(transport).bet_levels() == []
    # An absent filter means "all"; an empty array would say something else.
    assert transport.calls[0][3] == b'{"casino_id":"casinoxyz"}'


def test_a_refusal_is_an_api_error_keyed_on_api_code() -> None:
    message = "issue_id fs-1 was issued with other terms: valid_until"
    transport = Recorder(
        400,
        json.dumps(
            {"code": "invalid_argument", "msg": message, "meta": {"api_code": "610", "api_message": message}}
        ).encode(),
    )
    with pytest.raises(ApiError) as caught:
        client(transport).issue_freespins(ISSUE)
    assert caught.value.api_code == ApiCode.FREESPINS_INVALID_ISSUE
    assert not caught.value.is_retryable()
    assert "valid_until" in caught.value.api_message


def test_a_server_error_is_retryable() -> None:
    transport = Recorder(500, b'{"code":"internal","meta":{"api_code":"500"}}')
    with pytest.raises(ApiError) as caught:
        client(transport).cancel_freespins({"issue_id": "fs-1"})
    assert caught.value.is_retryable()


def test_launch_still_returns_the_url() -> None:
    transport = Recorder(200, b'{"launch_url":"https://games/x"}')
    url = client(transport).launch_real({"game": "dice", "account": {"id": "player_1", "currency": "EUR"}})
    assert url == "https://games/x"
