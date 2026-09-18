"""A complete, correct Beexar wallet held in memory.

Not durable, not shared between processes, not your ledger. It exists to show
the three rules that decide whether an integration is correct, in the order they
have to happen:

1. **Dedupe on ``id_provider`` first.** A repeat must return the transaction id
   and the balance you STORED the first time — not today's balance, not a fresh
   id. The platform retries, and a retry must be invisible.
2. **Then check the tombstone.** A rollback can arrive before the bet it
   reverses. When that bet finally shows up it must be refused with api_code
   409, not applied.
3. **Then apply, atomically.** Either every transaction in the request lands or
   none does, and an insufficient-funds answer reports the balance as it was
   BEFORE the batch — because nothing moved.

The lock below stands in for your ``BEGIN``/``COMMIT``. In a real wallet the
dedupe read, the tombstone read and the balance update must all be in one
database transaction. That is why the SDK does not offer to do idempotency for
you: it cannot join your transaction, so it could only pretend.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional

from beexar import (
    BalanceRequest,
    BalanceResult,
    BetWinRequest,
    BetWinResult,
    BetWinTransactionResult,
    FinishRequest,
    FinishResult,
    Money,
    RequestContext,
    RollbackRequest,
    RollbackResult,
    RollbackTransactionResult,
    WalletError,
)

_OP_TX_ID = re.compile(r"^op-tx-(\d+)$")


class InMemoryWallet:
    """The four callbacks over three dictionaries."""

    def __init__(self, state: Optional[Dict[str, Any]] = None) -> None:
        state = state or {}
        self._lock = threading.Lock()
        self._accounts: Dict[str, Dict[str, Any]] = {}
        self._transactions: Dict[str, Dict[str, str]] = {}
        self._tombstones: set = set()
        self._rounds: Dict[str, str] = {}
        self._next_round = 1

        for account_id, account in (state.get("accounts") or {}).items():
            self._accounts[account_id] = {
                "currency": account["currency"],
                "balance": Money.parse(account["balance"]),
                "bet_limited": bool(account.get("bet_limited", False)),
            }

        highest = 0
        for id_provider, transaction in (state.get("transactions") or {}).items():
            self._transactions[id_provider] = dict(transaction)
            match = _OP_TX_ID.match(transaction.get("id", ""))
            if match:
                highest = max(highest, int(match.group(1)))
        for id_provider in state.get("tombstones") or []:
            self._tombstones.add(id_provider)

        # Continue the id sequence rather than restarting it, so ids stay unique
        # across a restored state.
        self._next_tx = highest + 1

    def snapshot(self) -> Dict[str, Any]:
        """The current state, in the shape the constructor takes."""
        with self._lock:
            accounts: Dict[str, Any] = {}
            for account_id, account in self._accounts.items():
                entry = {"currency": account["currency"], "balance": str(account["balance"])}
                if account["bet_limited"]:
                    entry["bet_limited"] = True
                accounts[account_id] = entry
            return {
                "accounts": accounts,
                "transactions": {k: dict(v) for k, v in self._transactions.items()},
                "tombstones": sorted(self._tombstones),
            }

    # --- /balance ----------------------------------------------------------

    def balance(self, request: BalanceRequest, context: RequestContext) -> BalanceResult:
        with self._lock:
            return BalanceResult(balance=self._account(request.account_id)["balance"])

    # --- /betwin -----------------------------------------------------------

    def bet_win(self, request: BetWinRequest, context: RequestContext) -> BetWinResult:
        with self._lock:
            account = self._account(request.account_id)
            balance_before = account["balance"]

            # Everything is computed against a working copy first. Nothing
            # touches the stored state until the whole batch is known to succeed.
            running = balance_before
            replayed: Optional[Money] = None
            applied: List[Any] = []
            results: List[BetWinTransactionResult] = []

            for transaction in request.transactions:
                seen = self._transactions.get(transaction.id_provider)
                if seen is not None:
                    # 1. Replay: answer with what we stored, and change nothing.
                    results.append(BetWinTransactionResult(transaction.id_provider, seen["id"]))
                    replayed = Money.parse(seen["balance_after"])
                    continue
                if transaction.id_provider in self._tombstones:
                    # 2. Its rollback got here first.
                    raise WalletError.already_rolled_back()
                if transaction.type == "bet" and account["bet_limited"]:
                    raise WalletError.bet_limit_reached(balance_before)

                # 3. Apply on the working copy.
                if transaction.type == "bet":
                    after = running.sub(transaction.amount)
                    if after.is_negative():
                        # Report the balance as it stands — nothing was applied.
                        raise WalletError.insufficient_funds(balance_before)
                    running = after
                else:
                    running = running.add(transaction.amount)

                own_id = self._allocate_tx_id()
                applied.append(
                    (
                        transaction.id_provider,
                        {
                            "id": own_id,
                            "balance_after": str(running),
                            "type": transaction.type,
                            "amount": str(transaction.amount),
                        },
                    )
                )
                results.append(BetWinTransactionResult(transaction.id_provider, own_id))

            # Commit only if something was actually applied. A request made
            # entirely of repeats must leave the ledger exactly as it found it.
            balance = running
            if applied:
                for id_provider, stored in applied:
                    self._transactions[id_provider] = stored
                account["balance"] = running
            elif replayed is not None:
                balance = replayed

            return BetWinResult(
                round_id=self._round_id_for(request.round_id),
                balance=balance,
                transactions=results,
            )

    # --- /rollback ---------------------------------------------------------

    def rollback(self, request: RollbackRequest, context: RequestContext) -> RollbackResult:
        with self._lock:
            account = self._account(request.account_id)
            running = account["balance"]
            replayed: Optional[Money] = None
            changed = False
            results: List[RollbackTransactionResult] = []

            for transaction in request.transactions:
                seen = self._transactions.get(transaction.id_provider)
                if seen is not None:
                    results.append(RollbackTransactionResult(transaction.id_provider, seen["id"]))
                    replayed = Money.parse(seen["balance_after"])
                    continue

                # The tombstone goes down whether or not the original ever
                # arrived. That is what makes an out-of-order rollback safe:
                # when the bet turns up, the tombstone is already there.
                self._tombstones.add(transaction.original_id_provider)
                changed = True

                own_id = ""
                original = self._transactions.get(transaction.original_id_provider)
                if original is not None and original.get("type") and original.get("amount"):
                    amount = Money.parse(original["amount"])
                    if original["type"] == "bet":
                        running = running.add(amount)
                    else:
                        # Reversing a win can only take back what is there.
                        # Clamping at zero keeps a player from going negative
                        # because of our bookkeeping.
                        running = running.sub(amount).clamp_to_zero()
                    own_id = self._allocate_tx_id()

                self._transactions[transaction.id_provider] = {
                    "id": own_id,
                    "balance_after": str(running),
                }
                results.append(RollbackTransactionResult(transaction.id_provider, own_id))

            balance = running
            if changed:
                account["balance"] = running
            elif replayed is not None:
                balance = replayed

            return RollbackResult(
                balance=balance,
                round_id=self._round_id_for(request.round_id_provider),
                transactions=results,
            )

    # --- /finish -----------------------------------------------------------

    def finish(self, request: FinishRequest, context: RequestContext) -> FinishResult:
        # Nothing to settle: the round's money already moved through bet_win.
        # Answering the current balance and staying idempotent is the whole job.
        with self._lock:
            return FinishResult(balance=self._account(request.account_id)["balance"])

    # --- helpers -----------------------------------------------------------

    def _account(self, account_id: str) -> Dict[str, Any]:
        account = self._accounts.get(account_id)
        if account is None:
            raise WalletError.invalid_player("unknown account {}".format(account_id))
        return account

    def _allocate_tx_id(self) -> str:
        own_id = "op-tx-{}".format(self._next_tx)
        self._next_tx += 1
        return own_id

    def _round_id_for(self, provider_round_id: str) -> str:
        own = self._rounds.get(provider_round_id)
        if own is None:
            own = "op-round-{}".format(self._next_round)
            self._next_round += 1
            self._rounds[provider_round_id] = own
        return own
