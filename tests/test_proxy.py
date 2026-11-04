import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest

from tarka import Gateway
from tarka.proxy.app import create_app
from tarka.proxy.limits import MAX_REQUEST_BYTES

PROXY_KEY = "test-proxy-key-" + "x" * 32
TENANT_A_KEY = "test-tenant-a-key-" + "x" * 32
TENANT_B_KEY = "test-tenant-b-key-" + "y" * 32
AUTH_HEADERS = {"Authorization": f"Bearer {PROXY_KEY}"}


@asynccontextmanager
async def _proxy_client(
    gateway: Gateway,
    proxy_api_key: str | None = PROXY_KEY,
    tenant_api_keys: dict[str, str] | None = None,
) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(
        gateway,
        proxy_api_key=proxy_api_key if tenant_api_keys is None else None,
        tenant_api_keys=tenant_api_keys,
    )
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
                request = _request()
                constraints = request["logic_constraints"]
                assert isinstance(constraints, dict)
                request["logic_constraints"] = {
                    **constraints,
                    "schema": {
                        "type": "object",
                        "properties": {"applied_discount": {"type": "integer", "maximum": 20}},
                        "required": ["applied_discount"],
                    },
                }
                response = await client.post(
                    "/v1/chat/completions",
                    json=request,
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


def test_anthropic_messages_endpoint_translates_and_verifies() -> None:
    upstream_bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        upstream_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"applied_discount": 20}),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> httpx.Response:
        try:
            async with _proxy_client(gateway) as client:
                return await client.post(
                    "/v1/messages",
                    headers={
                        "x-api-key": PROXY_KEY,
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model": "test-model",
                        "max_tokens": 128,
                        "system": "Return JSON.",
                        "messages": [{"role": "user", "content": "Apply the discount."}],
                        "logic_constraints": {
                            "rules": ["applied_discount <= max_discount_percentage"],
                            "bindings": {"max_discount_percentage": 20},
                        },
                    },
                )
        finally:
            await upstream.aclose()

    response = asyncio.run(run())
    body = response.json()

    assert response.status_code == 200
    assert body["type"] == "message"
    assert body["stop_reason"] == "end_turn"
    assert body["content"] == [{"type": "text", "text": '{"applied_discount": 20}'}]
    assert body["usage"] == {"input_tokens": 10, "output_tokens": 3}
    sent_body = upstream_bodies[0]
    sent_messages = sent_body.get("messages")
    assert isinstance(sent_messages, list) and sent_messages
    assert sent_messages[0] == {"role": "system", "content": "Return JSON."}
    assert "logic_constraints" not in sent_body


def test_dashboard_metrics_are_authenticated_and_aggregate_only() -> None:
    completion = _completion(20)
    completion["usage"] = {"prompt_tokens": 12, "completion_tokens": 5}
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion))
    )
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> tuple[httpx.Response, httpx.Response]:
        try:
            async with _proxy_client(gateway) as client:
                unauthorized = await client.get("/dashboard/metrics")
                await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers=AUTH_HEADERS,
                )
                metrics = await client.get("/dashboard/metrics", headers=AUTH_HEADERS)
                return unauthorized, metrics
        finally:
            await upstream.aclose()

    unauthorized, response = asyncio.run(run())
    body = response.json()

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert body["requests_total"] == 1
    assert body["verified_passes"] == 1
    assert body["upstream_reported_input_tokens"] == 12
    assert body["upstream_reported_output_tokens"] == 5
    assert "messages" not in body
    assert "logic_constraints" not in body


def test_tenant_keys_isolate_requests_and_dashboard_metrics() -> None:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_completion(20)))
    )
    gateway = Gateway("https://llm.example/v1", client=upstream)
    tenant_keys = {"tenant-a": TENANT_A_KEY, "tenant-b": TENANT_B_KEY}

    async def run() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        try:
            async with _proxy_client(gateway, tenant_api_keys=tenant_keys) as client:
                await client.post(
                    "/v1/chat/completions",
                    json=_request(),
                    headers={"Authorization": f"Bearer {TENANT_A_KEY}"},
                )
                tenant_a_metrics = await client.get(
                    "/dashboard/metrics",
                    headers={"Authorization": f"Bearer {TENANT_A_KEY}"},
                )
                tenant_b_metrics = await client.get(
                    "/dashboard/metrics",
                    headers={"Authorization": f"Bearer {TENANT_B_KEY}"},
                )
                invalid_key = await client.get(
                    "/dashboard/metrics",
                    headers={"Authorization": "Bearer " + "z" * 40},
                )
                return tenant_a_metrics, tenant_b_metrics, invalid_key
        finally:
            await upstream.aclose()

    tenant_a, tenant_b, invalid = asyncio.run(run())

    assert tenant_a.status_code == tenant_b.status_code == 200
    assert tenant_a.json()["requests_total"] == 1
    assert tenant_a.json()["verified_passes"] == 1
    assert tenant_b.json()["requests_total"] == 0
    assert invalid.status_code == 401


def test_tenant_keys_require_unique_strong_credentials() -> None:
    gateway = Gateway("https://llm.example/v1")

    with pytest.raises(ValueError, match="unique"):
        create_app(
            gateway,
            tenant_api_keys={"tenant-a": TENANT_A_KEY, "tenant-b": TENANT_A_KEY},
        )
    with pytest.raises(ValueError, match="at least 32 characters"):
        create_app(gateway, tenant_api_keys={"tenant-a": "too-short"})


def test_authenticated_tenant_rate_limit_applies_to_both_provider_routes() -> None:
    upstream_calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json=_completion(20))

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        try:
            app = create_app(gateway, proxy_api_key=PROXY_KEY, rate_limit_per_minute=1)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    first = await client.post(
                        "/v1/chat/completions",
                        json=_request(),
                        headers=AUTH_HEADERS,
                    )
                    limited_anthropic = await client.post(
                        "/v1/messages",
                        json={
                            "model": "test-model",
                            "max_tokens": 64,
                            "messages": [{"role": "user", "content": "Hello."}],
                        },
                        headers={
                            "x-api-key": PROXY_KEY,
                            "anthropic-version": "2023-06-01",
                        },
                    )
                    metrics = await client.get("/dashboard/metrics", headers=AUTH_HEADERS)
                    return first, limited_anthropic, metrics
        finally:
            await upstream.aclose()

    first, limited, metrics = asyncio.run(run())

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert upstream_calls == 1
    assert metrics.json()["rate_limited_requests"] == 1
    assert metrics.json()["latency_sample_count"] == 1


def test_authenticated_tenants_have_independent_rate_limits() -> None:
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_completion(20)))
    )
    gateway = Gateway("https://llm.example/v1", client=upstream)

    async def run() -> tuple[httpx.Response, httpx.Response]:
        try:
            app = create_app(
                gateway,
                tenant_api_keys={"tenant-a": PROXY_KEY, "tenant-b": TENANT_B_KEY},
                rate_limit_per_minute=1,
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    first = await client.post(
                        "/v1/chat/completions",
                        json=_request(),
                        headers=AUTH_HEADERS,
                    )
                    second = await client.post(
                        "/v1/chat/completions",
                        json=_request(),
                        headers={"Authorization": f"Bearer {TENANT_B_KEY}"},
                    )
                    return first, second
        finally:
            await upstream.aclose()

    first, second = asyncio.run(run())
    assert first.status_code == 200
    assert second.status_code == 200


def test_invalid_rate_limit_configuration_is_rejected() -> None:
    gateway = Gateway("https://llm.example/v1")

    with pytest.raises(ValueError, match="requests_per_minute"):
        create_app(gateway, proxy_api_key=PROXY_KEY, rate_limit_per_minute=0)


def test_dashboard_page_is_static_and_does_not_cache_metrics_or_key() -> None:
    gateway = Gateway("https://llm.example/v1")

    async def run() -> httpx.Response:
        async with _proxy_client(gateway) as client:
            return await client.get("/dashboard")

    response = asyncio.run(run())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"]
    assert 'type="password"' in response.text
    assert "/dashboard/metrics" in response.text


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


def test_proxy_rejects_oversized_anthropic_request_body() -> None:
    gateway = Gateway("https://llm.example/v1")

    async def run() -> httpx.Response:
        async with _proxy_client(gateway) as client:
            return await client.post(
                "/v1/messages",
                content=b"x" * (MAX_REQUEST_BYTES + 1),
                headers={
                    "x-api-key": PROXY_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )

    response = asyncio.run(run())

    assert response.status_code == 413
    assert response.json()["error"]["type"] == "request_too_large"