"""Measure local verifier and proxy p95 latency with a mocked upstream."""

import asyncio
import json
import time
from collections.abc import Callable

import httpx

from tarka import Gateway, verify
from tarka.proxy.app import create_app


def _p95(samples: list[float]) -> float:
    ordered_samples = sorted(samples)
    index = (95 * len(ordered_samples) + 99) // 100 - 1
    return ordered_samples[index]


def _measure(action: Callable[[], object], *, warmups: int = 20, iterations: int = 250) -> float:
    for _ in range(warmups):
        action()

    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return _p95(samples)


def _measure_solver() -> float:
    bindings = {f"x{index}": index for index in range(100)}
    rules = [f"x{index} >= 0" for index in range(100)]

    def verify_sample() -> None:
        if not verify(bindings, rules).is_valid:
            raise RuntimeError("The benchmark verifier case unexpectedly failed.")

    return _measure(verify_sample)


def _measure_proxy() -> float:
    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"applied_discount": 20}),
                }
            }
        ]
    }
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion))
    )
    gateway = Gateway("https://mock.invalid/v1", client=upstream)
    api_key = "benchmark-key-" + "x" * 32
    request = {
        "model": "benchmark",
        "messages": [{"role": "user", "content": "verify"}],
        "logic_constraints": {
            "rules": ["applied_discount <= max_discount_percentage"],
            "bindings": {"max_discount_percentage": 20},
        },
    }

    app = create_app(gateway, proxy_api_key=api_key, rate_limit_per_minute=1000)

    async def run() -> float:
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    async def proxy_sample() -> None:
                        response = await client.post(
                            "/v1/chat/completions",
                            json=request,
                            headers={"Authorization": f"Bearer {api_key}"},
                        )
                        if response.status_code != 200:
                            raise RuntimeError(
                                f"Proxy benchmark failed: {response.status_code}."
                            )

                    for _ in range(20):
                        await proxy_sample()

                    samples = []
                    for _ in range(250):
                        started = time.perf_counter()
                        await proxy_sample()
                        samples.append((time.perf_counter() - started) * 1000)
                    return _p95(samples)
        finally:
            await upstream.aclose()

    return asyncio.run(run())


def main() -> None:
    print(f"solver_100_variables_p95_ms={_measure_solver():.3f}")
    print(f"proxy_mock_upstream_p95_ms={_measure_proxy():.3f}")


if __name__ == "__main__":
    main()