# TARKA

Deterministic logic gateway for agents. TARKA checks structured plans against
declared rules before they are returned to a caller.

## Development setup

TARKA requires Python 3.15 or newer and uses uv for environment and dependency
management.

```powershell
uv sync
uv run pytest
uv run ruff check
uv run ty check
```

## Verifier SDK

`verify()` checks a structured plan against numeric and boolean rules. A
violating plan returns `UNSAT` with the conflicting rule and known values.

```python
from tarka import VerificationStatus, verify

result = verify(
		{"applied_discount": 30, "max_discount_percentage": 20},
		rules=["applied_discount <= max_discount_percentage"],
)

assert result.status is VerificationStatus.UNSAT
print(result.message)
```

Run the local example with `uv run python examples/discount_order.py`.

## Local latency measurement

Run `uv run python examples/benchmark.py` to repeat the local p95 checks. On
2026-11-03, Windows with CPython 3.15.2 and an AMD Ryzen 9 5900HS measured
approximately 15.6 ms for 100 bound variables and 100 rules, and 3.0 ms for the
proxy with an in-process mock upstream (20 warmups, 250 measured requests). These
are local development measurements, not live-network, LLM, or AWS latency.

## HTTP proxy

Set the upstream OpenAI-compatible API URL and key, then start the proxy:

```powershell
$env:TARKA_UPSTREAM_BASE_URL = "https://api.openai.com/v1"
$secureUpstreamKey = Read-Host "Upstream API key" -AsSecureString
$env:TARKA_UPSTREAM_API_KEY = [System.Net.NetworkCredential]::new("", $secureUpstreamKey).Password
$secureProxyKey = Read-Host "Proxy API key (at least 32 characters)" -AsSecureString
$env:TARKA_PROXY_API_KEY = [System.Net.NetworkCredential]::new("", $secureProxyKey).Password
uv run uvicorn tarka.proxy.app:app --host 127.0.0.1 --port 8000
```

After stopping the server, clear the process environment variables:

```powershell
Remove-Item Env:TARKA_UPSTREAM_API_KEY,Env:TARKA_PROXY_API_KEY
```

Constrained completions must return a JSON object in assistant content or tool
arguments. Rules validate only the symbols they reference; include every
security-sensitive plan or tool argument in the declared rules. `logic_constraints`
is removed before the request reaches the upstream service.

```powershell
curl.exe http://127.0.0.1:8000/v1/chat/completions `
	-H "Authorization: Bearer $env:TARKA_PROXY_API_KEY" `
	-H "Content-Type: application/json" `
	--data-raw '{"model":"gpt-4o-mini","messages":[{"role":"system","content":"Return only a JSON object with the proposed applied_discount."},{"role":"user","content":"Apply a discount to this order."}],"logic_constraints":{"rules":["applied_discount <= max_discount_percentage"],"bindings":{"max_discount_percentage":20}}}'
```

`GET /healthz` returns `{"status":"ok"}` without authentication. Chat requests
require the configured bearer key, bodies are limited to 1 MiB, and streaming
is not supported. Keep `TARKA_FAIL_POLICY=fail_closed` for deployments; the
`fail_open` option can return unverified completions.

## AWS deployment

The Terraform baseline is private and requires an approved private DNS zone,
matching ACM certificate, allowed client CIDRs, and secured S3 state bucket.
See the [Terraform runbook](infra/terraform/README.md) for bootstrap, image
push, apply, and destroy steps. No AWS resources have been applied.
