"""OpenAI-compatible chat wrapper with deterministic verification and retries."""

import json
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

import httpx

from tarka.rules import parse_rule
from tarka.verifier import RuleEvaluationError, VerificationResult, VerificationStatus, verify

MAX_UPSTREAM_RESPONSE_BYTES = 4_194_304


class LogicViolationError(RuntimeError):
    """Raised when no verified completion is available under fail-closed policy."""

    def __init__(
        self,
        message: str,
        *,
        result: VerificationResult | None,
        attempts: int,
    ) -> None:
        super().__init__(message)
        self.result = result
        self.attempts = attempts


class TimeoutPolicyError(RuntimeError):
    """Raised when the solver cannot decide and the configured policy fails closed."""


class PlanExtractionError(ValueError):
    """Raised when an upstream completion does not contain a structured plan."""


class UpstreamProtocolError(RuntimeError):
    """Raised when the upstream response is not a valid chat-completion JSON object."""


class UnsupportedStreamingError(ValueError):
    """Raised when a streaming completion is requested through the verifier gateway."""


class Gateway:
    """Send chat requests upstream and return only verified constrained completions."""

    def __init__(
        self,
        upstream_base_url: str,
        *,
        api_key: str | None = None,
        max_retries: int = 2,
        fail_policy: Literal["fail_closed", "fail_open"] = "fail_closed",
        timeout: float = 30.0,
        max_response_bytes: int = MAX_UPSTREAM_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer.")
        if fail_policy not in ("fail_closed", "fail_open"):
            raise ValueError("fail_policy must be 'fail_closed' or 'fail_open'.")
        parsed_url = urlsplit(upstream_base_url)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        local_http = parsed_url.scheme == "http" and parsed_url.hostname in local_hosts
        if (
            not parsed_url.hostname
            or (parsed_url.scheme != "https" and not local_http)
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError(
                "upstream_base_url must use HTTPS; HTTP is allowed only for loopback."
            )
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer.")

        self._url = f"{upstream_base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._max_retries = max_retries
        self._fail_policy = fail_policy
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client when the gateway created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Gateway:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _same_json_value(left: object, right: object) -> bool:
        if type(left) is not type(right):
            return False
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            return left.keys() == right.keys() and all(
                Gateway._same_json_value(left[key], right[key]) for key in left
            )
        if isinstance(left, list) and isinstance(right, list):
            return len(left) == len(right) and all(
                Gateway._same_json_value(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        return left == right

    async def chat_completions(self, request: Mapping[str, object]) -> dict[str, object]:
        """Send an OpenAI chat-completions request, verifying it when constrained."""
        request_body = dict(request)
        if request_body.get("stream") is True:
            raise UnsupportedStreamingError("Streaming chat completions are not supported.")

        constraints = request_body.pop("logic_constraints", None)
        if constraints is None:
            return await self._send(request_body)
        if not isinstance(constraints, Mapping):
            raise ValueError("logic_constraints must be an object.")
        if type(request_body.get("n", 1)) is not int or request_body.get("n", 1) != 1:
            raise ValueError("Constrained chat requests must request exactly one completion.")

        rules = constraints.get("rules")
        if (
            not isinstance(rules, list)
            or not rules
            or not all(isinstance(rule, str) for rule in rules)
        ):
            raise ValueError("logic_constraints.rules must be a non-empty list of strings.")
        rule_list = tuple(rules)
        for rule in rule_list:
            parse_rule(rule)
        fixed_bindings = constraints.get("bindings", {})
        if not isinstance(fixed_bindings, Mapping) or not all(
            isinstance(name, str) for name in fixed_bindings
        ):
            raise ValueError("logic_constraints.bindings must be an object with string keys.")

        messages = request_body.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Constrained chat requests must include a messages array.")
        base_messages = list(messages)
        attempt_body = dict(request_body)
        last_completion: dict[str, object] | None = None
        last_result: VerificationResult | None = None
        feedback = "The response did not contain a structured plan. Return a JSON object."

        for attempt in range(self._max_retries + 1):
            completion = await self._send(attempt_body)
            last_completion = completion
            last_result = None
            try:
                plan = self._extract_plan(
                    completion,
                    self._allowed_tool_names(request_body.get("tools")),
                )
            except PlanExtractionError as exc:
                feedback = str(exc)
                last_result = None
            else:
                changed_fixed_values = [
                    name
                    for name, value in fixed_bindings.items()
                    if name in plan and not self._same_json_value(plan[name], value)
                ]
                if changed_fixed_values:
                    names = ", ".join(sorted(changed_fixed_values))
                    feedback = (
                        f"Do not change fixed input bindings: {names}. Return a corrected plan."
                    )
                    last_result = None
                else:
                    all_bindings = dict(fixed_bindings)
                    all_bindings.update(plan)
                    try:
                        last_result = verify(all_bindings, rule_list)
                    except RuleEvaluationError as exc:
                        feedback = str(exc)
                    else:
                        if last_result.status is VerificationStatus.SAT:
                            return completion
                        if last_result.status is VerificationStatus.UNKNOWN:
                            if self._fail_policy == "fail_open":
                                return completion
                            raise TimeoutPolicyError(
                                "The rule solver could not decide: "
                                f"{last_result.reason or 'unknown reason'}."
                            )
                        feedback = last_result.message or (
                            "The proposed plan violates the declared rules."
                        )

            if attempt < self._max_retries:
                attempt_body["messages"] = [
                    *base_messages,
                    {
                        "role": "user",
                        "content": (
                            "The proposed plan was rejected by the deterministic logic verifier. "
                            f"{feedback} Revise the plan and return only the corrected JSON object."
                        ),
                    },
                ]

        if self._fail_policy == "fail_open" and last_completion is not None:
            return last_completion
        raise LogicViolationError(
            feedback,
            result=last_result,
            attempts=self._max_retries + 1,
        )

    async def _send(self, request_body: dict[str, object]) -> dict[str, object]:
        response_body = bytearray()
        async with self._client.stream(
            "POST",
            self._url,
            json=request_body,
            headers=self._headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if len(response_body) + len(chunk) > self._max_response_bytes:
                    raise UpstreamProtocolError("The upstream response exceeded the size limit.")
                response_body.extend(chunk)
        try:
            completion = json.loads(response_body)
        except ValueError as exc:
            raise UpstreamProtocolError("The upstream response was not valid JSON.") from exc
        if not isinstance(completion, Mapping):
            raise UpstreamProtocolError("The upstream response must be a JSON object.")
        return dict(completion)

    @classmethod
    def _extract_plan(
        cls,
        completion: Mapping[str, object],
        allowed_tool_names: set[str],
    ) -> dict[str, object]:
        choices = completion.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise PlanExtractionError("A constrained response must contain exactly one choice.")
        if not isinstance(choices[0], Mapping):
            raise PlanExtractionError("The upstream response has no chat completion choice.")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise PlanExtractionError("The upstream response has no assistant message.")

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            if len(tool_calls) != 1:
                raise PlanExtractionError("Only one tool call per completion can be verified.")
            tool_call = tool_calls[0]
            if not isinstance(tool_call, Mapping):
                raise PlanExtractionError("A tool call must be an object.")
            function = tool_call.get("function")
            if not isinstance(function, Mapping):
                raise PlanExtractionError("A tool call must include function arguments.")
            function_name = function.get("name")
            if not isinstance(function_name, str) or function_name not in allowed_tool_names:
                raise PlanExtractionError(
                    "The completion called a tool not declared by the request."
                )
            return cls._parse_plan_object(function.get("arguments"))

        content = message.get("content")
        if not isinstance(content, str):
            raise PlanExtractionError("The assistant response must contain a JSON plan.")
        return cls._parse_plan_object(content)

    @staticmethod
    def _allowed_tool_names(tools: object) -> set[str]:
        if not isinstance(tools, list):
            return set()
        names = set()
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            function = tool.get("function")
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            if isinstance(name, str):
                names.add(name)
        return names

    @staticmethod
    def _parse_plan_object(value: object) -> dict[str, object]:
        try:
            plan = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError as exc:
            raise PlanExtractionError("The assistant response is not valid JSON.") from exc
        if not isinstance(plan, Mapping) or not all(isinstance(name, str) for name in plan):
            raise PlanExtractionError("The structured plan must be a JSON object with string keys.")
        return dict(plan)