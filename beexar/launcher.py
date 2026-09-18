"""The calls you send to Beexar: launch a session, list the catalogue."""

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
    """Launch sessions and read the catalogue.

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
        return self._post_signed("/api/v1/softswiss/launcher/real", request)

    def launch_demo(self, request: Dict[str, Any]) -> str:
        """Launch a demo session on a virtual balance. No wallet callbacks."""
        return self._post_signed("/api/v1/softswiss/launcher/demo", request)

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

    def _post_signed(self, path: str, payload: Dict[str, Any]) -> str:
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
        return str(decoded.get("launch_url", ""))

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
