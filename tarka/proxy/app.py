"""FastAPI OpenAI-compatible proxy backed by the TARKA Gateway."""

import os
import secrets
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from json import JSONDecodeError, loads
from math import ceil
from threading import Lock
from time import perf_counter
from typing import Annotated

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tarka.gateway import (
    Gateway,
    LogicViolationError,
    TimeoutPolicyError,
    UnsupportedStreamingError,
    UpstreamProtocolError,
)
from tarka.proxy.anthropic import (
    AnthropicProtocolError,
    from_openai_response,
    to_openai_request,
)
from tarka.proxy.dashboard import DASHBOARD_HTML
from tarka.proxy.limits import MAX_REQUEST_BYTES, RequestBodyLimitMiddleware
from tarka.proxy.metrics import GatewayMetrics, TenantRateLimiter


class LogicConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[Annotated[str, Field(max_length=4096)]] = Field(
        default_factory=list,
        max_length=100,
    )
    templates: list[Annotated[str, Field(max_length=128)]] = Field(
        default_factory=list,
        max_length=8,
    )
    bindings: dict[str, object] = Field(default_factory=dict, max_length=256)
    json_schema: dict[str, object] | bool | None = Field(default=None, alias="schema")
    openapi: dict[str, object] | None = None
    operation_id: str | None = Field(default=None, max_length=256)
    workflow: dict[str, object] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, object]]
    logic_constraints: LogicConstraints | None = None


def _configured_gateway() -> Gateway:
    policy = os.getenv("TARKA_FAIL_POLICY", "fail_closed")
    if policy not in ("fail_closed", "fail_open"):
        raise ValueError("TARKA_FAIL_POLICY must be 'fail_closed' or 'fail_open'.")
    return Gateway(
        upstream_base_url=os.getenv(
            "TARKA_UPSTREAM_BASE_URL",
            "http://127.0.0.1:8001/v1",
        ),
        api_key=os.getenv("TARKA_UPSTREAM_API_KEY"),
        max_retries=int(os.getenv("TARKA_MAX_RETRIES", "2")),
        fail_policy=policy,
    )


def _configured_tenant_keys() -> dict[str, str] | None:
    value = os.getenv("TARKA_TENANT_API_KEYS")
    if value is None:
        return None
    try:
        tenant_keys = loads(value)
    except JSONDecodeError as exc:
        raise ValueError("TARKA_TENANT_API_KEYS must be a JSON object.") from exc
    if not isinstance(tenant_keys, Mapping) or not tenant_keys:
        raise ValueError("TARKA_TENANT_API_KEYS must be a non-empty JSON object.")
    if len(tenant_keys) > 100:
        raise ValueError("TARKA_TENANT_API_KEYS may configure at most 100 tenants.")
    normalized: dict[str, str] = {}
    for tenant_id, api_key in tenant_keys.items():
        if (
            not isinstance(tenant_id, str)
            or not tenant_id
            or len(tenant_id) > 128
            or not isinstance(api_key, str)
            or len(api_key) < 32
        ):
            raise ValueError("Each tenant requires an ID and an API key of at least 32 characters.")
        normalized[tenant_id] = api_key
    if len(set(normalized.values())) != len(normalized):
        raise ValueError("Tenant API keys must be unique.")
    return normalized


def create_app(
    gateway: Gateway | None = None,
    *,
    proxy_api_key: str | None = None,
    tenant_api_keys: Mapping[str, str] | None = None,
    rate_limit_per_minute: int | None = None,
) -> FastAPI:
    """Create the proxy app, optionally using an injected Gateway for tests."""
    active_gateway = gateway or _configured_gateway()
    configured_proxy_key = (
        proxy_api_key
        if proxy_api_key is not None
        else os.getenv("TARKA_PROXY_API_KEY")
    )
    configured_tenant_keys = (
        dict(tenant_api_keys) if tenant_api_keys is not None else _configured_tenant_keys()
    )
    if configured_tenant_keys is not None:
        if configured_proxy_key:
            raise ValueError(
                "Configure either TARKA_PROXY_API_KEY or TARKA_TENANT_API_KEYS, not both."
            )
        if not configured_tenant_keys or len(configured_tenant_keys) > 100:
            raise ValueError("tenant_api_keys must configure between 1 and 100 tenants.")
        for tenant_id, api_key in configured_tenant_keys.items():
            if (
                not isinstance(tenant_id, str)
                or not tenant_id
                or len(tenant_id) > 128
                or not isinstance(api_key, str)
                or len(api_key) < 32
            ):
                raise ValueError(
                    "Each tenant requires an ID and an API key of at least 32 characters."
                )
        if len(set(configured_tenant_keys.values())) != len(configured_tenant_keys):
            raise ValueError("Tenant API keys must be unique.")
    if configured_proxy_key and len(configured_proxy_key) < 32:
        raise ValueError("TARKA_PROXY_API_KEY must contain at least 32 characters.")
    if rate_limit_per_minute is None:
        raw_rate_limit = os.getenv("TARKA_RATE_LIMIT_PER_MINUTE", "60")
        try:
            rate_limit_per_minute = int(raw_rate_limit)
        except ValueError as exc:
            raise ValueError("TARKA_RATE_LIMIT_PER_MINUTE must be an integer.") from exc

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await active_gateway.aclose()

    app = FastAPI(title="TARKA Gateway", lifespan=lifespan)
    app.state.gateway = active_gateway
    tenant_metrics: dict[str, GatewayMetrics] = {}
    metrics_lock = Lock()
    rate_limiter = TenantRateLimiter(rate_limit_per_minute)

    def metrics_for(tenant_id: str) -> GatewayMetrics:
        with metrics_lock:
            if tenant_id not in tenant_metrics:
                tenant_metrics[tenant_id] = GatewayMetrics()
            return tenant_metrics[tenant_id]

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

    def authenticated_tenant(authorization: str | None) -> str | None:
        if configured_tenant_keys is not None:
            matched_tenant = None
            supplied = (authorization or "").encode("utf-8")
            for tenant_id, api_key in configured_tenant_keys.items():
                expected = f"Bearer {api_key}".encode()
                if secrets.compare_digest(supplied, expected):
                    matched_tenant = tenant_id
            return matched_tenant
        if not configured_proxy_key:
            return None
        expected = f"Bearer {configured_proxy_key}".encode()
        supplied = (authorization or "").encode("utf-8")
        return "default" if secrets.compare_digest(supplied, expected) else None

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/dashboard/metrics")
    async def dashboard_metrics(
        authorization: Annotated[str | None, Header()] = None,
    ) -> object:
        if not configured_proxy_key and configured_tenant_keys is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "type": "proxy_auth_not_configured",
                        "message": "The proxy API key is not configured.",
                    }
                },
            )
        tenant_id = authenticated_tenant(authorization)
        if tenant_id is None:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "error": {
                        "type": "unauthorized",
                        "message": "A valid bearer token is required.",
                    }
                },
            )
        return metrics_for(tenant_id).snapshot()

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(
            DASHBOARD_HTML,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> object:
        if not configured_proxy_key and configured_tenant_keys is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "type": "proxy_auth_not_configured",
                        "message": "The proxy API key is not configured.",
                    }
                },
            )
        tenant_id = authenticated_tenant(authorization)
        if tenant_id is None:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "error": {
                        "type": "unauthorized",
                        "message": "A valid bearer token is required.",
                    }
                },
            )
        retry_after = rate_limiter.acquire(tenant_id)
        if retry_after is not None:
            metrics_for(tenant_id).record("rate_limited", 0, include_latency=False)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(max(1, ceil(retry_after)))},
                content={
                    "error": {
                        "type": "rate_limit_exceeded",
                        "message": "Request rate limit exceeded.",
                    }
                },
            )
        try:
            request_started = perf_counter()
            metrics = metrics_for(tenant_id)
            completion = await active_gateway.chat_completions(
                request.model_dump(exclude_none=True, by_alias=True)
            )
            metrics.record(
                (
                    "unconstrained"
                    if request.logic_constraints is None
                    else (
                        "verified"
                        if active_gateway.fail_policy == "fail_closed"
                        else "fail_open_completion"
                    )
                ),
                (perf_counter() - request_started) * 1000,
                completion,
            )
            return completion
        except UnsupportedStreamingError as exc:
            metrics.record("other_error", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "unsupported_streaming", "message": str(exc)}},
            )
        except LogicViolationError as exc:
            metrics.record("logic_violation", (perf_counter() - request_started) * 1000)
            result = exc.result
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "type": "logic_violation",
                        "message": str(exc),
                        "attempts": exc.attempts,
                        "unsat_core": list(result.unsat_core) if result else [],
                    }
                },
            )
        except TimeoutPolicyError as exc:
            metrics.record("timeout", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=503,
                content={"error": {"type": "verification_timeout", "message": str(exc)}},
            )
        except (httpx.HTTPError, UpstreamProtocolError):
            metrics.record("upstream_error", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "type": "upstream_error",
                        "message": "The upstream chat service request failed.",
                    }
                },
            )
        except ValueError as exc:
            metrics.record("other_error", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=422,
                content={"error": {"type": "invalid_request", "message": str(exc)}},
            )

    @app.post("/v1/messages")
    async def anthropic_messages(
        request: dict[str, object],
        authorization: Annotated[str | None, Header()] = None,
        x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
        anthropic_version: Annotated[str | None, Header(alias="anthropic-version")] = None,
    ) -> object:
        if not configured_proxy_key and configured_tenant_keys is None:
            return JSONResponse(
                status_code=503,
                content={
                    "type": "error",
                    "error": {
                        "type": "proxy_auth_not_configured",
                        "message": "The proxy API key is not configured.",
                    },
                },
            )
        client_authorization = authorization
        if client_authorization is None and x_api_key is not None:
            client_authorization = f"Bearer {x_api_key}"
        tenant_id = authenticated_tenant(client_authorization)
        if tenant_id is None:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "Invalid API key."},
                },
            )
        retry_after = rate_limiter.acquire(tenant_id)
        if retry_after is not None:
            metrics_for(tenant_id).record("rate_limited", 0, include_latency=False)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(max(1, ceil(retry_after)))},
                content={
                    "type": "error",
                    "error": {
                        "type": "rate_limit_exceeded",
                        "message": "Request rate limit exceeded.",
                    },
                },
            )
        if not anthropic_version:
            return JSONResponse(
                status_code=400,
                content={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "The anthropic-version header is required.",
                    },
                },
            )
        try:
            upstream_request = to_openai_request(request)
        except AnthropicProtocolError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": str(exc)},
                },
            )

        request_started = perf_counter()
        try:
            metrics = metrics_for(tenant_id)
            completion = await active_gateway.chat_completions(upstream_request)
            try:
                translated = from_openai_response(
                    completion,
                    requested_model=str(upstream_request["model"]),
                )
            except AnthropicProtocolError as exc:
                raise UpstreamProtocolError(str(exc)) from exc
            metrics.record(
                (
                    "verified"
                    if request.get("logic_constraints") is not None
                    and active_gateway.fail_policy == "fail_closed"
                    else (
                        "fail_open_completion"
                        if request.get("logic_constraints") is not None
                        else "unconstrained"
                    )
                ),
                (perf_counter() - request_started) * 1000,
                completion,
            )
            return translated
        except UnsupportedStreamingError as exc:
            metrics.record("other_error", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=400,
                content={
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": str(exc)},
                },
            )
        except LogicViolationError as exc:
            metrics.record("logic_violation", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=422,
                content={
                    "type": "error",
                    "error": {"type": "logic_violation", "message": str(exc)},
                },
            )
        except TimeoutPolicyError as exc:
            metrics.record("timeout", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=503,
                content={
                    "type": "error",
                    "error": {"type": "verification_timeout", "message": str(exc)},
                },
            )
        except (httpx.HTTPError, UpstreamProtocolError):
            metrics.record("upstream_error", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=502,
                content={
                    "type": "error",
                    "error": {
                        "type": "upstream_error",
                        "message": "The upstream chat service request failed.",
                    },
                },
            )
        except ValueError as exc:
            metrics.record("other_error", (perf_counter() - request_started) * 1000)
            return JSONResponse(
                status_code=422,
                content={
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": str(exc)},
                },
            )

    return app


app = create_app()