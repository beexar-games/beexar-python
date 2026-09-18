# Changelog

All notable changes to `beexar`. The version is shared across the Beexar SDKs
for Node, PHP, Go and Python — the same number always means the same contract
snapshot.

## 1.0.0 — unreleased

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
