# Changelog

All notable changes to `beexar`. The version is shared across the Beexar SDKs
for Node, PHP, Go and Python — the same number always means the same contract
snapshot.

## 1.2.0 — 2026-10-23

Operator freespins, the SoftSwiss Freespins API one to one. Additions only: a
handler that does not issue freespins runs unchanged.

- `Client.issue_freespins`, `Client.cancel_freespins` and `Client.bet_levels` —
  Freespins/Issue, Freespins/Cancel and Bet/Levels, signed over the exact bytes
  like the launch calls.
- `/freespins/finish`, the fifth callback: give your handler the optional
  `freespins_finish(request, context)` to receive a `FreespinsFinishRequest`
  (`amount` as `Money`, zero allowed) and answer a `FreespinsFinishResult`.
  Without it the route answers like an unknown one.
- `WalletError.invalid_freespins_issue()` (api_code 610) and
  `WalletError.freespins_state_transition()` (605); the freespins api_codes in
  `ApiCode`.
- `ApiCode.FEATURE_NOT_SUPPORTED` (608): `issue_freespins` answers it for a
  game that plays no freespins, and `ApiCode.GAME_NOT_AVAILABLE` (405) for a
  game your casino does not have; 601 is left for a missing bet level. 406
  reads "This feature is not enabled for your casino" (the SoftSwiss registry
  of 2026-10-25).
- `ROUTES` gains `"/freespins/finish"`, and `fastapi_router` and
  `flask_blueprint` register it with the other four.
- `route_from_path` matches the longest route first. `/freespins/finish` ends
  with `/finish` too, and must not reach the round's finish handler.
- The reference wallet credits a freespins result once per issue id, and the
  conformance fixtures (version 2) cover it.
- A malformed `X-REQUEST-SIGN` of the right length but not hex — 64 Latin-1
  letters, say — is refused like any bad signature (HTTP 400, api_code 403). It
  used to raise `TypeError` out of `hmac.compare_digest`, which left the
  bindings with an unhandled exception. `verify` now requires 64 lowercase hex
  characters before comparing. Conformance fixtures version 3 add
  `0033-signature-non-ascii`.
- The game catalog no longer lists the built-in slots game, removed on
  2026-10-24: `category` is `dice`, `plinko`, `crash` or `rgs`. `has_freespins`
  reports a game's own in-game free spins, not operator freespins; no game
  sets it today.

## 1.1.0 — 2026-10-04

Python 3.11 or newer, up from 3.9: 3.9 and 3.10 are past end of life upstream.
pip on an older Python keeps resolving 1.0.2. No change to the API.

## 1.0.2 — 2026-09-18

No change to the code you consume. 1.0.1 reached PyPI, Packagist and the Go
proxy but not npm: the publish workflow pinned `npm@latest`, which had moved to
a major that refuses to run on the Node version the workflow used.

## 1.0.1 — 2026-09-18

No change to the code you consume. The release exists to move publishing onto
npm's and PyPI's trusted publishing, so no long-lived registry token is stored
anywhere any more.

## 1.0.0 — 2026-09-18

First public release.

- `Client` — `launch_real`, `launch_demo`, `list_games`, signed with HMAC-SHA256
  over the exact request bytes.
- `WalletServer` — the four seamless-wallet callbacks as one pure
  `dispatch(route, raw_body, signature)`.
- Bindings for ASGI, FastAPI and Flask, each dealing with that framework's own
  way of destroying the raw body.
- `Money` — decimal amounts that cannot be built from a float.
- `WalletError` — the full api_code registry, with the balance a required
  argument for codes 100, 105 and 106.
- No runtime dependencies.
