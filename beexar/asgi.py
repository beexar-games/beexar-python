"""Framework bindings: ASGI, FastAPI and Flask.

Each one exists to solve the same problem in that framework's own way — getting
the SDK the **raw bytes** of the request. The signature is an HMAC over those
bytes; a framework that parses the JSON and hands you a dict has already
destroyed them, and no care afterwards can bring them back.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .signature import SIGNATURE_HEADER
from .wallet import WalletServer, route_from_path

__all__ = ["asgi_app", "fastapi_router", "flask_blueprint"]


def asgi_app(server: WalletServer) -> Callable[..., Any]:
    """A bare ASGI application serving the four callbacks.

    Mount it under any prefix; the routes are matched on the path suffix.
    """

    async def app(scope: Dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            raise RuntimeError("beexar: the wallet app only handles HTTP scopes")

        route = route_from_path(scope.get("path", "/"))
        if route is None or scope.get("method") != "POST":
            await _send_json(send, 404, b'{"code":"invalid_argument","msg":"not found",'
                                        b'"meta":{"api_code":"404","api_message":"not found"}}')
            return

        # Read the body as bytes. Never decode it here: the bytes are what the
        # signature covers.
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        out = server.dispatch(route, body, headers.get(SIGNATURE_HEADER.lower()), headers)
        await _send_json(send, out.status, out.body)

    return app


async def _send_json(send: Callable[..., Any], status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def fastapi_router(server: WalletServer, prefix: str = "") -> Any:
    """A FastAPI ``APIRouter`` with the four callbacks.

    The handlers declare ``Request`` and call ``await request.body()`` rather
    than a typed model. Declaring a model would make Starlette parse the JSON,
    and the bytes the signature covers would be gone before the SDK ever saw
    them. Starlette caches the body, so reading it here is safe.
    """
    from fastapi import APIRouter, Request, Response  # imported lazily: FastAPI is optional

    router = APIRouter(prefix=prefix)

    def make(route: str) -> Callable[..., Any]:
        async def endpoint(request: Request) -> Response:
            body = await request.body()
            headers = {k.lower(): v for k, v in request.headers.items()}
            out = server.dispatch(route, body, headers.get(SIGNATURE_HEADER.lower()), headers)
            return Response(content=out.body, status_code=out.status, media_type="application/json")

        return endpoint

    for route in ("/balance", "/betwin", "/rollback", "/finish"):
        router.add_api_route(route, make(route), methods=["POST"])

    return router


def flask_blueprint(server: WalletServer, name: str = "beexar_wallet", url_prefix: Optional[str] = None) -> Any:
    """A Flask ``Blueprint`` with the four callbacks.

    Uses ``request.get_data(cache=True)``, which yields ``bytes``.
    ``get_data(as_text=True)`` re-decodes the body and is the usual way a Flask
    integration loses its signature.
    """
    from flask import Blueprint, Response, request  # imported lazily: Flask is optional

    blueprint = Blueprint(name, __name__, url_prefix=url_prefix)

    def make(route: str) -> Callable[..., Any]:
        def endpoint() -> Any:
            body = request.get_data(cache=True)
            headers = {k.lower(): v for k, v in request.headers.items()}
            out = server.dispatch(route, body, headers.get(SIGNATURE_HEADER.lower()), headers)
            return Response(out.body, status=out.status, mimetype="application/json")

        endpoint.__name__ = "beexar" + route.replace("/", "_")
        return endpoint

    for route in ("/balance", "/betwin", "/rollback", "/finish"):
        blueprint.add_url_rule(route, view_func=make(route), methods=["POST"])

    return blueprint
