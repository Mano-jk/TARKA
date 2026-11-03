import asyncio
import json

import httpx
import pytest

from tarka import (
    Gateway,
    LogicViolationError,
    TimeoutPolicyError,
    UpstreamProtocolError,
    VerificationResult,
    VerificationStatus,
)


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


def test_gateway_requires_https_for_remote_upstreams() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Gateway("http://llm.example/v1")


def test_gateway_allows_loopback_http_for_local_development() -> None:
    gateway = Gateway("http://localhost:8001/v1")

    asyncio.run(gateway.aclose())


def _request() -> dict[str, object]:
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Apply the discount."}],
        "logic_constraints": {
            "rules": ["applied_discount <= max_discount_percentage"],
            "bindings": {"max_discount_percentage": 20},
        },
    }


def test_gateway_rejects_number_that_does_not_match_fixed_boolean() -> None:
    request_bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"flag": 1}),
                        }
                    }
                ]
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = Gateway("https://llm.example/v1", max_retries=1, client=client)
            await gateway.chat_completions(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "Check the flag."}],
                    "logic_constraints": {
                        "rules": ["flag == 1"],
                        "bindings": {"flag": True},
                    },
                }
            )

    with pytest.raises(LogicViolationError, match="fixed input bindings") as error:
        asyncio.run(run())

    assert error.value.attempts == 2
    assert error.value.result is None
    assert len(request_bodies) == 2


def test_gateway_clears_stale_result_when_retry_has_missing_bindings() -> None:
    responses = [
        _completion(30),
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"other_value": 10}),
                    }
                }
            ]
        },
    ]
    request_count = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        completion = responses[request_count]
        request_count += 1
        return httpx.Response(200, json=completion)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = Gateway("https://llm.example/v1", max_retries=1, client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(
        LogicViolationError, match="Missing bindings for: applied_discount"
    ) as error:
        asyncio.run(run())

    assert error.value.attempts == 2
    assert error.value.result is None
    assert request_count == 2


def test_gateway_retries_unsat_plan_and_strips_constraints_upstream() -> None:
    request_bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_bodies.append(body)
        return httpx.Response(200, json=_completion(30 if len(request_bodies) == 1 else 20))

    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = Gateway("https://llm.example/v1", client=client)
            return await gateway.chat_completions(_request())

    completion = asyncio.run(run())

    assert completion == _completion(20)
    assert len(request_bodies) == 2
    assert all("logic_constraints" not in body for body in request_bodies)
    retry_messages = request_bodies[1].get("messages")
    assert isinstance(retry_messages, list) and retry_messages
    retry_message = retry_messages[-1]
    assert isinstance(retry_message, dict)
    assert retry_message.get("role") == "user"
    feedback = retry_message.get("content")
    assert isinstance(feedback, str)
    assert "applied_discount=30" in feedback


def test_gateway_fails_closed_after_retry_limit() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(30))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = Gateway("https://llm.example/v1", max_retries=1, client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(LogicViolationError) as error:
        asyncio.run(run())

    assert error.value.attempts == 2
    assert error.value.result is not None


def test_gateway_default_retry_budget_is_two() -> None:
    upstream_calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json=_completion(30))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = Gateway("https://llm.example/v1", client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(LogicViolationError) as error:
        asyncio.run(run())

    assert error.value.attempts == 3
    assert upstream_calls == 3


def test_solver_unknown_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def unknown_result(*_: object, **__: object) -> VerificationResult:
        return VerificationResult(VerificationStatus.UNKNOWN, reason="timeout")

    monkeypatch.setattr("tarka.gateway.verify", unknown_result)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_completion(20)))
        ) as client:
            gateway = Gateway("https://llm.example/v1", client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(TimeoutPolicyError, match="timeout"):
        asyncio.run(run())


def test_solver_unknown_obeys_fail_open_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    def unknown_result(*_: object, **__: object) -> VerificationResult:
        return VerificationResult(VerificationStatus.UNKNOWN, reason="timeout")

    monkeypatch.setattr("tarka.gateway.verify", unknown_result)

    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_completion(20)))
        ) as client:
            gateway = Gateway("https://llm.example/v1", fail_policy="fail_open", client=client)
            return await gateway.chat_completions(_request())

    assert asyncio.run(run()) == _completion(20)


def test_gateway_extracts_tool_call_arguments() -> None:
    tool_completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "apply_discount",
                                "arguments": json.dumps({"applied_discount": 20}),
                            }
                        }
                    ],
                }
            }
        ]
    }

    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=tool_completion))
        ) as client:
            gateway = Gateway("https://llm.example/v1", client=client)
            request = _request()
            request["tools"] = [
                {"type": "function", "function": {"name": "apply_discount"}}
            ]
            return await gateway.chat_completions(request)

    assert asyncio.run(run()) == tool_completion


def test_gateway_rejects_undeclared_tool_calls() -> None:
    tool_completion = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "transfer_funds",
                                "arguments": json.dumps({"amount": 10}),
                            }
                        }
                    ]
                }
            }
        ]
    }

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=tool_completion))
        ) as client:
            gateway = Gateway("https://llm.example/v1", max_retries=0, client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(LogicViolationError, match="tool not declared"):
        asyncio.run(run())


def test_gateway_rejects_multiple_completion_choices() -> None:
    valid_choices = _completion(20).get("choices")
    invalid_choices = _completion(30).get("choices")
    assert isinstance(valid_choices, list) and isinstance(invalid_choices, list)
    completion = {"choices": [valid_choices[0], invalid_choices[0]]}

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion))
        ) as client:
            gateway = Gateway("https://llm.example/v1", max_retries=0, client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(LogicViolationError, match="exactly one choice"):
        asyncio.run(run())


def test_gateway_rejects_n_greater_than_one_before_upstream_call() -> None:
    upstream_calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json=_completion(20))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = Gateway("https://llm.example/v1", client=client)
            request = _request()
            request["n"] = 2
            await gateway.chat_completions(request)

    with pytest.raises(ValueError, match="exactly one completion"):
        asyncio.run(run())
    assert upstream_calls == 0


def test_gateway_rejects_multiple_tool_calls() -> None:
    multiple_tools = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"arguments": json.dumps({"applied_discount": 20})}},
                        {"function": {"arguments": json.dumps({"transfer_amount": 1})}},
                    ]
                }
            }
        ]
    }

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=multiple_tools))
        ) as client:
            gateway = Gateway("https://llm.example/v1", max_retries=0, client=client)
            await gateway.chat_completions(_request())

    with pytest.raises(LogicViolationError, match="Only one tool call"):
        asyncio.run(run())


def test_gateway_rejects_oversized_upstream_response() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"x" * 65)
            )
        ) as client:
            gateway = Gateway(
                "https://llm.example/v1",
                max_response_bytes=64,
                client=client,
            )
            await gateway.chat_completions({"model": "test-model", "messages": []})

    with pytest.raises(UpstreamProtocolError, match="exceeded the size limit"):
        asyncio.run(run())


def test_fail_open_returns_the_last_unverified_completion() -> None:
    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_completion(30)))
        ) as client:
            gateway = Gateway(
                "https://llm.example/v1",
                max_retries=0,
                fail_policy="fail_open",
                client=client,
            )
            return await gateway.chat_completions(_request())

    assert asyncio.run(run()) == _completion(30)