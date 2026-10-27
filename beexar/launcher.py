"""The calls you send to Beexar: launch a session, list the catalogue, issue
and cancel freespins, read the bet levels they are issued at."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .errors import ApiError
from .signature import SIGNATURE_HEADER, sign

__all__ = ["Client", "PRODUCTION_BASE_URL"]

PRODUCTION_BASE_URL = "https://gateway.beexar.com"


class Client:
    """Launch sessions, read the catalogue, issue and cancel freespins.

    Every POST is signed with ``hex(HMAC-SHA256(body, api_secret))`` over the
    exact bytes that go on the wire — the body is encoded once, signed, and
    sent. Encoding twice is how signatures mysteriously stop matching.

    Transport is :mod:`urllib` from the standard library, so the SDK pulls in no
    HTTP client. Pass ``transport`` to use requests, httpx, or anything else; it
    receives the final bytes and headers.
    """

    def __init__(
        self,
        casino_id: str,
        api_secret: str,
        base_url: str = PRODUCTION_BASE_URL,
        timeout: float = 10.0,
        transport: Optional[Any] = None,
    ) -> None:
        if not casino_id:
            raise ValueError("Client: casino_id is required")
        if not api_secret:
            raise ValueError("Client: api_secret is required")
        self._casino_id = casino_id
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # transport(method, url, headers, body) -> (status, response_bytes)
        self._transport = transport

    def launch_real(self, request: Dict[str, Any]) -> str:
        """Launch a real-money session and return the URL to embed."""
        return str(self._post_signed("/api/v1/softswiss/launcher/real", request).get("launch_url", ""))

    def launch_demo(self, request: Dict[str, Any]) -> str:
        """Launch a demo session on a virtual balance. No wallet callbacks."""
        return str(self._post_signed("/api/v1/softswiss/launcher/demo", request).get("launch_url", ""))

    def list_games(
        self, operator: Optional[str] = None, active: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """The games enabled for your operator. Public — no signature."""
        query = {"operator": operator or self._casino_id}
        if active is not None:
            query["active"] = "true" if active else "false"
        url = "{}/api/v1/operator/games?{}".format(self._base_url, urllib.parse.urlencode(query))

        status, body = self._send("GET", url, {"Accept": "application/json"}, None)
        decoded = _decode(body)
        if status != 200:
            raise ApiError.from_envelope(status, decoded)
        games = decoded.get("games") or []
        return list(games)

    def issue_freespins(self, request: Dict[str, Any]) -> None:
        """Freespins/Issue: give a player free rounds in the listed games.

        ``request`` carries ``issue_id``, ``games``, ``freespins_quantity``,
        ``bet_level`` (one from :meth:`bet_levels`), ``valid_until`` (UTC with a
        ``Z``, at most one month ahead) and ``account`` (the launch account —
        its ``id`` and ``currency`` decide whose freespins these are).

        The player finds them on their next :meth:`launch_real` of one of those
        games; what they win arrives once, on your ``/freespins/finish``
        callback. Record the issue on your side before calling this — the
        result names only ``issue_id``. A repeat with the same terms answers
        like the first call, so retry a timeout or a 5xx with the SAME request:
        a ``valid_until`` computed again is other terms, and other terms under
        one ``issue_id`` answer 610.
        """
        # The answer is an empty object; a 200 is all there is to read.
        self._post_signed("/api/v1/softswiss/freespins/issue", request)

    def cancel_freespins(self, request: Dict[str, Any]) -> None:
        """Freespins/Cancel (``{"issue_id": ...}``), at any time — even mid-play.

        A canceled issue never reaches ``/freespins/finish``: what it won is
        forfeited. Canceling an issue that has already been played out is
        refused with api_code 620.
        """
        self._post_signed("/api/v1/softswiss/freespins/cancel", request)

    def bet_levels(
        self, games: Optional[List[str]] = None, currencies: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Bet/Levels: the bet levels freespins can be issued at.

        One entry per group of games whose levels are identical in every
        currency: ``{"games": [...], "currencies": [{"currency": "EUR",
        "bet_levels": [{"bet_level": 1, "amount": "0.10"}, ...]}]}``. ``amount``
        is the TOTAL bet of one round, a decimal string — ``Money.parse`` it for
        arithmetic. Unknown games and currencies are ignored; leave a filter out
        for all of them.
        """
        request: Dict[str, Any] = {}
        if games is not None:
            request["games"] = list(games)
        if currencies is not None:
            request["currencies"] = list(currencies)
        levels = self._post_signed("/api/v1/softswiss/bet/levels", request).get("bet_levels") or []
        return list(levels)

    def _post_signed(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body_dict = dict(payload)
        body_dict["casino_id"] = self._casino_id

        # Encode ONCE. The signature and the request body are the same bytes, so
        # they cannot disagree.
        body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        status, response = self._send(
            "POST",
            self._base_url + path,
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                SIGNATURE_HEADER: sign(body, self._api_secret),
            },
            body,
        )
        decoded = _decode(response)
        if status != 200:
            raise ApiError.from_envelope(status, decoded)
        return decoded

    def _send(
        self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes]
    ) -> "tuple[int, bytes]":
        if self._transport is not None:
            return self._transport(method, url, headers, body)

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            # A 400 here is an answer, not a transport failure — read it.
            return error.code, error.read()


def _decode(body: bytes) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"msg": body.decode("utf-8", "replace")}
    return decoded if isinstance(decoded, dict) else {"msg": str(decoded)}
