"""In-memory aggregate gateway metrics without retaining request content."""

import math
import threading
import time
from collections import Counter, deque
from collections.abc import Mapping

_LATENCY_WINDOW = 1024
_TOKEN_BUCKET_TENANTS = 1000
_OUTCOMES = frozenset(
    {
        "verified",
        "unconstrained",
        "fail_open_completion",
        "logic_violation",
        "timeout",
        "upstream_error",
        "other_error",
        "rate_limited",
    }
)


class GatewayMetrics:
    """Track bounded, process-local outcome and latency aggregates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._outcomes: Counter[str] = Counter()
        self._latencies_ms: deque[float] = deque(maxlen=_LATENCY_WINDOW)
        self._input_tokens = 0
        self._output_tokens = 0

    def record(
        self,
        outcome: str,
        latency_ms: float,
        completion: Mapping[str, object] | None = None,
        *,
        include_latency: bool = True,
    ) -> None:
        """Record one aggregate result; prompt and completion bodies are discarded."""
        if outcome not in _OUTCOMES:
            raise ValueError(f"Unknown gateway metric outcome: {outcome!r}.")
        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("latency_ms must be a finite non-negative value.")

        input_tokens, output_tokens = self._reported_tokens(completion)
        with self._lock:
            self._outcomes[outcome] += 1
            if include_latency:
                self._latencies_ms.append(latency_ms)
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens

    def snapshot(self) -> dict[str, int | float]:
        """Return aggregate counters and a bounded-window latency summary."""
        with self._lock:
            latencies = sorted(self._latencies_ms)
            count = sum(self._outcomes.values())
            return {
                "requests_total": count,
                "verified_passes": self._outcomes["verified"],
                "unconstrained_requests": self._outcomes["unconstrained"],
                "fail_open_completions": self._outcomes["fail_open_completion"],
                "logic_violations": self._outcomes["logic_violation"],
                "verification_timeouts": self._outcomes["timeout"],
                "upstream_errors": self._outcomes["upstream_error"],
                "other_errors": self._outcomes["other_error"],
                "rate_limited_requests": self._outcomes["rate_limited"],
                "mean_request_latency_ms": (
                    sum(latencies) / len(latencies) if latencies else 0.0
                ),
                "p95_request_latency_ms": self._percentile(latencies, 0.95),
                "latency_sample_count": len(latencies),
                "upstream_reported_input_tokens": self._input_tokens,
                "upstream_reported_output_tokens": self._output_tokens,
            }

    @staticmethod
    def _reported_tokens(completion: Mapping[str, object] | None) -> tuple[int, int]:
        usage = completion.get("usage") if completion is not None else None
        if not isinstance(usage, Mapping):
            return 0, 0
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        if type(input_tokens) is not int or input_tokens < 0:
            input_tokens = 0
        if type(output_tokens) is not int or output_tokens < 0:
            output_tokens = 0
        return input_tokens, output_tokens

    @staticmethod
    def _percentile(sorted_values: list[float], percentile: float) -> float:
        if not sorted_values:
            return 0.0
        index = max(0, math.ceil(percentile * len(sorted_values)) - 1)
        return sorted_values[index]


class TenantRateLimiter:
    """Bounded process-local token bucket keyed by authenticated tenant."""

    def __init__(self, requests_per_minute: int) -> None:
        if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 10_000:
            raise ValueError("requests_per_minute must be an integer from 1 to 10000.")
        self._capacity = float(requests_per_minute)
        self._refill_per_second = self._capacity / 60
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str) -> float | None:
        """Consume a token or return retry delay seconds."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(tenant_id)
            tokens, previous = (
                (self._capacity, now)
                if bucket is None
                else bucket
            )
            tokens = min(self._capacity, tokens + (now - previous) * self._refill_per_second)
            if tokens >= 1:
                self._buckets[tenant_id] = (tokens - 1, now)
                return None
            if bucket is None and len(self._buckets) >= _TOKEN_BUCKET_TENANTS:
                return 60 / self._refill_per_second
            self._buckets[tenant_id] = (tokens, now)
            return (1 - tokens) / self._refill_per_second
