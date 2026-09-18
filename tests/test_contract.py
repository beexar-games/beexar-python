"""The Python SDK has no code generator: openapi-generator is a JAR, and this
pipeline has no Docker-in-Docker to run one in. What replaces it is this test.

``contract.json`` is produced from the four OpenAPI documents by
``sdk/tools/gen-contract.mjs``. Here we assert that the SDK's own view of the
contract still matches it. A field added to ``wallet.yaml`` fails this test
rather than reaching an operator as a silently dropped value.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any, Dict

import pytest

from beexar import (
    ROUTES,
    ApiCode,
    BalanceRequest,
    BetWinRequest,
    BetWinTransaction,
    CURRENCY_PATTERN,
    FinishRequest,
    RollbackRequest,
    RollbackTransaction,
    is_funds_related_code,
)

from conftest import conformance_root

CONTRACT: Dict[str, Any] = json.loads((conformance_root() / "contract.json").read_text())


def test_every_spec_is_versioned() -> None:
    for path in (
        "api/providers/softswiss/wallet.yaml",
        "api/softswiss/gateway.yaml",
        "api/gateway.yaml",
        "api/common/schemas.yaml",
    ):
        assert path in CONTRACT["contracts"]
        assert re.match(r"^v\.\d{4}\.\d{2}\.\d{2}$", CONTRACT["contracts"][path])


@pytest.mark.parametrize(
    "schema,dto,renamed",
    [
        ("PlayerBalanceRequest", BalanceRequest, {}),
        ("RoundBetWinRequest", BetWinRequest, {}),
        ("RoundBetWinRequestTransaction", BetWinTransaction, {}),
        ("RoundRollbackRequest", RollbackRequest, {}),
        ("RoundRollbackRequestTransaction", RollbackTransaction, {"type": None}),
        ("RoundFinishRequest", FinishRequest, {}),
    ],
)
def test_every_property_in_the_spec_reaches_the_handler(
    schema: str, dto: type, renamed: Dict[str, Any]
) -> None:
    """A property the spec declares must exist on the dataclass the handler sees.

    ``RoundRollbackRequestTransaction.type`` is the one documented exception: the
    contract fixes it to the constant "rollback", so the SDK validates it and
    does not hand a field back that can only have one value.
    """
    wallet = CONTRACT["schemas"]["wallet"]
    assert schema in wallet, "{} vanished from the spec".format(schema)
    fields = {f.name for f in dataclasses.fields(dto)}

    for prop in wallet[schema]["properties"]:
        if prop in renamed and renamed[prop] is None:
            continue
        assert prop in fields, "{}.{} is in the spec but not on {}".format(schema, prop, dto.__name__)


def test_currency_pattern_matches_the_spec() -> None:
    from_spec = CONTRACT["schemas"]["wallet"]["PlayerBalanceRequest"]["properties"]["currency"]["pattern"]
    assert from_spec == CURRENCY_PATTERN


def test_routes_are_the_four_the_contract_defines() -> None:
    assert ROUTES == ("/balance", "/betwin", "/rollback", "/finish")


def test_funds_related_codes_are_exactly_the_three() -> None:
    # The platform reads meta.balance with the error swallowed, so nothing
    # downstream complains if we get this wrong — which is why it is pinned.
    for code in (ApiCode.INSUFFICIENT_FUNDS, ApiCode.BET_LIMIT_REACHED, ApiCode.MAX_BET_EXCEEDED):
        assert is_funds_related_code(code)
    for code in (ApiCode.INVALID_PLAYER, ApiCode.FORBIDDEN, ApiCode.ALREADY_ROLLED_BACK, ApiCode.BAD_REQUEST):
        assert not is_funds_related_code(code)
