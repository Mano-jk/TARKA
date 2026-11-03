import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx

from tarka import Gateway
from tarka.proxy.app import create_app
from tarka.proxy.limits import MAX_REQUEST_BYTES

PROXY_KEY = "test-proxy-key-" + "x" * 32
AUTH_HEADERS = {"Authorization": f"Bearer {PROXY_KEY}"}


@asynccontextmanager
async def _proxy_client(
    gateway: Gateway,
    proxy_api_key: str | None = PROXY_KEY,
) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(gateway, proxy_api_key=proxy_api_key)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client


def _completion(discount: int) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"applied_discount": discount}),
                }
            }
        ]
    }


def _request() -> dict[str, object]:
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Apply the discount."}],
        "logic_constraints": {
            "rules": ["applied_discount <= max_discount_percentage"],
            "bindings": {"max_discount_percentage": 20},
        },
    }


def test_proxy_health_and_constraint_stripping() -> None:
    upstream_bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        upstream_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(20))

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> tuple[dict[str, str], httpx.Response]:
        try:
            async with _proxy_client(gateway) as client:
                health = (await client.get("/healthz")).json()
                response = await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers=AUTH_HEADERS,
                )
                return health, response
        finally:
            await upstream.aclose()

    health, response = asyncio.run(run())

    assert health == {"status": "ok"}
    assert response.status_code == 200
    assert response.json() == _completion(20)
    assert len(upstream_bodies) == 1
    assert "logic_constraints" not in upstream_bodies[0]


def test_proxy_returns_structured_error_after_invalid_plan() -> None:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=_completion(30))
        )
    )
    gateway = Gateway("https://llm.example/v1", max_retries=0, client=upstream)

    async def run() -> httpx.Response:
        try:
            async with _proxy_client(gateway) as client:
                return await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers=AUTH_HEADERS,
                )
        finally:
            await upstream.aclose()

    response = asyncio.run(run())

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "logic_violation"
    assert response.json()["error"]["unsat_core"] == [
        "applied_discount <= max_discount_percentage"
    ]


def test_proxy_rejects_constrained_streaming() -> None:
    gateway = Gateway("https://llm.example/v1")

    async def run() -> httpx.Response:
        async with _proxy_client(gateway) as client:
            return await client.post(
                "/v1/chat/completions",
                json={**_request(), "stream": True},
                headers=AUTH_HEADERS,
            )

    response = asyncio.run(run())

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "unsupported_streaming"


def test_proxy_maps_malformed_upstream_json_to_502() -> None:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
        )
    )
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> httpx.Response:
        try:
            async with _proxy_client(gateway) as client:
                return await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers=AUTH_HEADERS,
                )
        finally:
            await upstream.aclose()

    response = asyncio.run(run())

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_error"


def test_proxy_requires_bearer_authentication() -> None:
    upstream_calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json=_completion(20))

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        try:
            async with _proxy_client(gateway) as client:
                unauthorized = await client.post("/v1/chat/completions", json=_request())
                invalid = await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers={"Authorization": "Bearer wrong"},
                )
                authorized = await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers=AUTH_HEADERS,
                )
                return unauthorized, invalid, authorized
        finally:
            await upstream.aclose()

    unauthorized, invalid, authorized = asyncio.run(run())

    assert unauthorized.status_code == 401
    assert invalid.status_code == 401
    assert authorized.status_code == 200
    assert upstream_calls == 1


def test_proxy_without_configured_auth_fails_closed() -> None:
    gateway = Gateway("https://llm.example/v1")

    async def run() -> httpx.Response:
        async with _proxy_client(gateway, proxy_api_key="") as client:
            return await client.post("/v1/chat/completions", json=_request())

    response = asyncio.run(run())

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "proxy_auth_not_configured"


def test_proxy_rejects_oversized_request_body() -> None:
    gateway = Gateway("https://llm.example/v1")

    async def run() -> httpx.Response:
        async with _proxy_client(gateway) as client:
            return await client.post(
                "/v1/chat/completions",
                content=b"x" * (MAX_REQUEST_BYTES + 1),
                headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            )

    response = asyncio.run(run())

    assert response.status_code == 413
    assert response.json()["error"]["type"] == "request_too_large"