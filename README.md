# beexar

Beexar operator SDK for Python. Launch game sessions, and serve the four
seamless-wallet callbacks the platform calls during play.

**Zero runtime dependencies** — standard library only. Python 3.11+.

```bash
pip install beexar
```

Full docs: **https://docs.beexar.com** · OpenAPI: **https://docs.beexar.com/api-reference/**

---

## The integration in one picture

There are two halves, and the second one is the work.

```
you  ──  POST /api/v1/softswiss/launcher/real  ──▶  Beexar     (Client)
                                                      │
player plays                                          │
                                                      ▼
your wallet  ◀──  POST /balance /betwin /rollback /finish  ──  Beexar   (WalletServer)
```

## Half 1 — launching a game

```python
from beexar import Client

beexar = Client(os.environ["BEEXAR_CASINO_ID"], os.environ["BEEXAR_API_SECRET"])

launch_url = beexar.launch_real({
    "game": "dice",
    "account": {"id": "player_123", "currency": "EUR"},
    "locale": "en",
})
# put launch_url in an iframe
```

`launch_demo()` does the same on a virtual balance and makes no wallet calls.
`list_games()` returns the catalogue enabled for you.

## Half 2 — serving the wallet

Implement four methods against your ledger. Everything else — signature
verification, parsing, validation, the error envelope — is handled.

```python
from beexar import BetWinResult, BetWinTransactionResult, WalletError, WalletServer

class Wallet:
    def bet_win(self, request, context):
        with self.db.transaction():  # ONE transaction. See "The boundary".
            results = []
            for t in request.transactions:
                seen = self.lookup(t.id_provider)
                if seen:
                    results.append(BetWinTransactionResult(t.id_provider, seen.id))
                    continue
                if self.is_rolled_back(t.id_provider):
                    raise WalletError.already_rolled_back()
                if t.type == "bet" and self.balance().compare(t.amount) < 0:
                    raise WalletError.insufficient_funds(self.balance())
                results.append(BetWinTransactionResult(t.id_provider, self.apply(t)))
            return BetWinResult(self.round_id(request.round_id), self.balance(), results)

    # … balance(), rollback(), finish()

server = WalletServer(Wallet(), os.environ["BEEXAR_API_SECRET"])
```

Mount it:

```python
from beexar.asgi import fastapi_router
app.include_router(fastapi_router(server, prefix="/wallet"))   # FastAPI

from beexar.asgi import flask_blueprint
app.register_blueprint(flask_blueprint(server, url_prefix="/wallet"))   # Flask

from beexar.asgi import asgi_app
application = asgi_app(server)   # bare ASGI
```

Then point the four callback URLs in the backoffice at
`https://your-host/wallet/{balance,betwin,rollback,finish}` and run the
[Integration Test Game](https://docs.beexar.com/guides/testing/) — 29 scenarios
against your implementation.

A complete, correct wallet you can read in one sitting:
[`examples/inmemory_wallet.py`](./examples/inmemory_wallet.py). A runnable
server: [`examples/server.py`](./examples/server.py).

## The raw body — read this one

The signature is an HMAC over the **exact bytes** of the request. If anything
parses the JSON and serialises it again before the SDK sees it, those bytes are
gone — key order, escaping and number rendering all change — and no signature
can ever match again.

| Framework | The trap | What the binding does |
|---|---|---|
| FastAPI | declaring a Pydantic model makes Starlette parse the body and you never see bytes | declares `Request`, calls `await request.body()` |
| Flask | `request.get_data(as_text=True)` re-decodes the body | `request.get_data(cache=True)` → bytes |
| ASGI | building the body by concatenating decoded chunks corrupts multi-byte characters | concatenates `bytes` |

`dispatch()` refuses a `str` outright — a decode is not always reversible, and
verifying a lossy copy is worse than failing. When a signature fails and the SDK
can tell why, the `on_warning` callback receives the reason in plain words.

## Money

Amounts and balances are decimal strings in the currency's main unit — `"0.90"`
is ninety cents. `Money` cannot be built from a float, raises on `float()`, and
refuses `+` and `-` so an accidental mix with a number is impossible.

```python
str(Money.parse("100.00").sub(Money.parse("0.30")))   # "99.70"
str(Money.from_minor_units(9970, 2))                  # "99.70" from an integer ledger
```

`"1E2000000000"` and anything past 16 decimals is rejected on shape, before any
arithmetic touches it.

## Errors

Two HTTP statuses exist on this contract, 400 and 500, and the meaning lives in
`meta.api_code`. Raise a `WalletError` and the envelope is built for you:

```python
raise WalletError.insufficient_funds(current_balance)  # 400 / api_code 100
raise WalletError.already_rolled_back()                # 400 / api_code 409
raise WalletError.invalid_player()                     # 400 / api_code 101
```

Codes 100, 105 and 106 **must** carry the player's balance, so those
constructors take it as a required argument — there is no way to build one
without it. Anything else you raise becomes an opaque 500 and your message never
leaves the process.

## The boundary

The SDK does **not** do idempotency or tombstones for you, and it will not
pretend to. Both have to happen in the same database transaction as the balance
update, and no library can join your transaction. What you must do:

1. Store every `id_provider` with a unique index, and check it **inside** the
   transaction that moves the money.
2. Store the response you returned — a repeat must return the id and balance you
   gave the first time, not today's.
3. On rollback, record a tombstone for `original_id_provider` **whether or not**
   the original exists. Out-of-order delivery is normal; a later `/betwin` for a
   tombstoned id must be refused with `WalletError.already_rolled_back()`.

Your handler has **15 seconds**. The platform retries 5xx and timeouts for up to
30 seconds with the same `id_provider`; it does not retry insufficient funds,
bet limits, bad requests or signature failures.

## Freespins (optional)

Freespins follow the SoftSwiss Freespins API one to one: you give a player N
free rounds at a fixed bet, Beexar plays them without touching the wallet, and
reports what they won once, when the issue is over. Three calls go out from
you; one callback comes back.

```python
from datetime import datetime, timedelta, timezone

# The bet levels each game offers, per currency. A level is the total bet of one round.
levels = beexar.bet_levels(games=["dice"], currencies=["EUR"])

# Record the issue on YOUR side first — the result will name only its issue_id.
ledger.record_freespins_issue("fs-123", "player_123", "EUR")
beexar.issue_freespins({
    "issue_id": "fs-123",
    "games": ["dice"],
    "freespins_quantity": 10,
    "bet_level": 1,
    # UTC with a Z, at most one month ahead
    "valid_until": (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "account": {"id": "player_123", "currency": "EUR"},
})
# The player launches the game as usual (launch_real) and finds them there.

beexar.cancel_freespins({"issue_id": "fs-123"})  # any time; a canceled issue is never reported
```

A repeated `issue_freespins` with the same terms answers like the first, so
retry a timeout with the **same** dict — a recomputed `valid_until` is other
terms, refused with api_code 610.

The result arrives on a fifth callback, `/freespins/finish`, carrying the issue
id and the amount won — zero included. Give your handler one more method:

```python
from beexar import FreespinsFinishResult, WalletError

class Wallet:
    # … balance, bet_win, rollback, finish

    def freespins_finish(self, request, context):
        with self.db.transaction():  # ONE transaction, like bet_win
            issue = self.freespins_issue(request.issue_id)
            if issue is None:
                raise WalletError.invalid_freespins_issue()        # api_code 610
            if issue.finished:
                return FreespinsFinishResult(issue.balance_after)  # a retry: credit nothing
            if issue.canceled:
                raise WalletError.freespins_state_transition()     # api_code 605: Beexar accepted your cancel
            return FreespinsFinishResult(self.credit(issue, request.amount))
```

- It is sent **once per issue** — when the issue is played out, or expires with
  spins played — and retried with the same body (after 1, 3 and 5 minutes, then
  every 5) until you answer 200. Each attempt has the same **15 seconds** as the
  round callbacks. Credit once per issue id; a repeat returns the balance you
  stored the first time.
- Answer `605` only for an issue whose cancel Beexar accepted — Beexar never
  sends a Finish for one; a `605` to a Finish you did receive is retried like
  any refusal.
- Configure it in the backoffice as the operator's **freespins finish URL**, the
  fifth callback URL next to the four, e.g.
  `https://your-host/wallet/freespins/finish`. Leave it empty if you do not
  offer freespins — `issue_freespins` then answers api_code 406.
- A handler without `freespins_finish` keeps working unchanged; the route then
  answers like an unknown one (api_code 404).
- Paths are matched on their suffix, longest route first, so
  `/wallet/freespins/finish` and `/wallet/finish` live under one prefix; the
  FastAPI and Flask bindings register the route too.

The reference wallet implements it end to end:
[`examples/inmemory_wallet.py`](./examples/inmemory_wallet.py).

## Types come from the spec

The dataclasses track the published OpenAPI documents (`openapi/` in this repo).
`tests/test_contract.py` reads a normalised description of those specs and fails
if a property the contract declares never reaches your handler.

## License

MIT
