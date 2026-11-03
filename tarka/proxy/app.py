"""FastAPI OpenAI-compatible proxy backed by the TARKA Gateway."""

import os
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tarka.gateway import (
    Gateway,
    LogicViolationError,
    TimeoutPolicyError,
    UnsupportedStreamingError,
    UpstreamProtocolError,
)
from tarka.proxy.limits import MAX_REQUEST_BYTES, RequestBodyLimitMiddleware


class LogicConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[Annotated[str, Field(max_length=4096)]] = Field(min_length=1, max_length=100)
    bindings: dict[str, object] = Field(default_factory=dict, max_length=256)


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


def create_app(
    gateway: Gateway | None = None,
    *,
    proxy_api_key: str | None = None,
) -> FastAPI:
    """Create the proxy app, optionally using an injected Gateway for tests."""
    active_gateway = gateway or _configured_gateway()
    configured_proxy_key = (
        proxy_api_key if proxy_api_key is not None else os.getenv("TARKA_PROXY_API_KEY")
    )
    if configured_proxy_key and len(configured_proxy_key) < 32:
        raise ValueError("TARKA_PROXY_API_KEY must contain at least 32 characters.")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await active_gateway.aclose()

    app = FastAPI(title="TARKA Gateway", lifespan=lifespan)
    app.state.gateway = active_gateway
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> object:
        if not configured_proxy_key:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "type": "proxy_auth_not_configured",
                        "message": "The proxy API key is not configured.",
                    }
                },
            )
        expected = f"Bearer {configured_proxy_key}".encode()
        supplied = (authorization or "").encode("utf-8")
        if not secrets.compare_digest(supplied, expected):
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
        try:
            return await active_gateway.chat_completions(request.model_dump(exclude_none=True))
        except UnsupportedStreamingError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "unsupported_streaming", "message": str(exc)}},
            )
        except LogicViolationError as exc:
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
            return JSONResponse(
                status_code=503,
                content={"error": {"type": "verification_timeout", "message": str(exc)}},
            )
        except (httpx.HTTPError, UpstreamProtocolError):
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
            return JSONResponse(
                status_code=422,
                content={"error": {"type": "invalid_request", "message": str(exc)}},
            )

    return app


app = create_app()