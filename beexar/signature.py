"""HMAC-SHA256 request signing, over the raw bytes and nothing else."""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional, Union

__all__ = ["SIGNATURE_HEADER", "sign", "verify"]

#: Hex-encoded HMAC-SHA256 of the RAW request body, keyed with the operator's
#: API secret. 64 lowercase hex characters.
SIGNATURE_HEADER = "X-REQUEST-SIGN"


def _as_bytes(body: Union[bytes, bytearray, str]) -> bytes:
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def sign(body: Union[bytes, bytearray, str], secret: str) -> str:
    """Sign the exact bytes you are about to put on the wire."""
    return hmac.new(secret.encode("utf-8"), _as_bytes(body), hashlib.sha256).hexdigest()


def verify(body: Union[bytes, bytearray, str], signature: Optional[str], secret: str) -> bool:
    """Constant-time signature check.

    ``body`` must be the bytes as received. If anything between the socket and
    this call parsed the JSON and serialised it again, those bytes are gone and
    no amount of care here can recover them.
    """
    if not signature:
        return False
    return hmac.compare_digest(sign(body, secret), signature)
