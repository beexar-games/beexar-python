"""The cross-language conformance suite.

The same fixtures are run by the Node, Go and PHP SDKs, so behaviour cannot
drift between languages while there is only one set of cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from beexar import RequestContext, WalletServer, sign
from inmemory_wallet import InMemoryWallet

from conftest import conformance_root

ROOT = conformance_root()

MANIFEST: Dict[str, Any] = json.loads((ROOT / "manifest.json").read_text())
SECRET = (ROOT / "secret.txt").read_text()


class ExplodingWallet:
    """Makes the ``boom`` account raise something that is not a WalletError.

    That exercises the "handler raised the unexpected" path without putting a
    test hook in the reference wallet itself.
    """

    def __init__(self, wallet: InMemoryWallet) -> None:
        self._wallet = wallet

    def balance(self, request, context):  # type: ignore[no-untyped-def]
        if request.account_id == "boom":
            raise RuntimeError("simulated ledger outage")
        return self._wallet.balance(request, context)

    def bet_win(self, request, context):  # type: ignore[no-untyped-def]
        return self._wallet.bet_win(request, context)

    def rollback(self, request, context):  # type: ignore[no-untyped-def]
        return self._wallet.rollback(request, context)

    def finish(self, request, context):  # type: ignore[no-untyped-def]
        return self._wallet.finish(request, context)


def _normalise(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "accounts": ledger.get("accounts") or {},
        "transactions": ledger.get("transactions") or {},
        "tombstones": sorted(ledger.get("tombstones") or []),
    }


def test_the_manifest_is_the_full_set() -> None:
    assert len(MANIFEST["cases"]) > 20


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c: c["dir"])
def test_case(case: Dict[str, Any]) -> None:
    directory = ROOT / "cases" / case["dir"]
    body = (directory / "request.body").read_bytes()
    request = json.loads((directory / "request.json").read_text())
    before = json.loads((directory / "ledger.before.json").read_text())
    after = json.loads((directory / "ledger.after.json").read_text())
    expected = json.loads((directory / "response.json").read_text())

    # A fixture whose stored signature does not match its own bytes would
    # quietly test nothing, so the suite re-derives it.
    signature = request["headers"].get("X-REQUEST-SIGN")
    if signature and signature != "0" * 64:
        assert signature == sign(body, SECRET), "fixture signature does not match its own body"

    wallet = InMemoryWallet(before)
    server = WalletServer(ExplodingWallet(wallet), SECRET)

    out = server.dispatch(case["endpoint"], body, signature)

    assert out.status == expected["status"], out.body
    assert json.loads(out.body) == expected["body"]
    assert _normalise(wallet.snapshot()) == _normalise(after)
