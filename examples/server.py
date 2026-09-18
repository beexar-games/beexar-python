"""A complete Beexar wallet integration in one file, with no framework.

    BEEXAR_API_SECRET=... python examples/server.py

Point the four callback URLs in the backoffice at
http://<host>/wallet/{balance,betwin,rollback,finish} and run the Integration
Test Game against it.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from beexar import MAX_BODY_BYTES, SIGNATURE_HEADER, WalletServer, route_from_path  # noqa: E402
from inmemory_wallet import InMemoryWallet  # noqa: E402

API_SECRET = os.environ.get("BEEXAR_API_SECRET", "")
if not API_SECRET:
    raise SystemExit("BEEXAR_API_SECRET is not set — refusing to start without a secret")

WALLET = InMemoryWallet({"accounts": {"player_1": {"currency": "EUR", "balance": "1000.00"}}})
SERVER = WalletServer(WALLET, API_SECRET, on_warning=lambda message: print("beexar:", message))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's naming
        route = route_from_path(self.path)
        if route is None:
            self._json(404, b'{"code":"invalid_argument","msg":"not found",'
                            b'"meta":{"api_code":"404","api_message":"not found"}}')
            return

        # Read at most one byte past the cap: enough to know the body is too
        # large, without paying for the rest of what a sender claims to have.
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY_BYTES + 1)
        body = self.rfile.read(length)

        headers = {k.lower(): v for k, v in self.headers.items()}
        out = SERVER.dispatch(route, body, headers.get(SIGNATURE_HEADER.lower()), headers)
        self._json(out.status, out.body)

    def _json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print("beexar wallet:", fmt % args)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print("beexar wallet listening on :{}".format(port))
    print("  POST /wallet/balance  /wallet/betwin  /wallet/rollback  /wallet/finish")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
