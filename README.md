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

The typed TypeScript proxy client is in `sdk/typescript`. From that directory,
run `npm ci`, `npm test`, and `npm pack --dry-run`. It uses the platform
`fetch` implementation and sends `logic_constraints` only to TARKA.

## Local latency measurement

Run `uv run python examples/benchmark.py` to repeat the local p95 checks. On
2026-11-04, Windows with CPython 3.15.2 and an AMD Ryzen 9 5900HS measured
approximately 16.8 ms for 100 bound variables and 100 rules, and 3.6 ms for the
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
Remove-Item Env:TARKA_UPSTREAM_API_KEY,Env:TARKA_PROXY_API_KEY,Env:TARKA_TENANT_API_KEYS
```

Constrained completions must return a JSON object in assistant content or tool
arguments. Rules validate only the symbols they reference; include every
security-sensitive plan or tool argument in the declared rules. `logic_constraints`
is removed before the request reaches the upstream service. Constraints may use
numeric/boolean `rules`, a Draft 2020-12 JSON Schema under `schema`, or both. A
schema-only request is sufficient to enable constrained verification:

```json
{
  "logic_constraints": {
    "schema": {
      "type": "object",
      "properties": {"applied_discount": {"type": "number", "maximum": 20}},
      "required": ["applied_discount"],
      "additionalProperties": false
    }
  }
}
```

Schema violations are returned to the upstream model as correction feedback and
are subject to the same retry and fail-closed policy as rule violations.
For OpenAPI 3.1.0/3.1.1 contracts, pass the document and an operation ID instead
of `schema`. TARKA validates against that operation's JSON request-body schema.
Only local JSON Pointer references are accepted; recursive and external schema
references are rejected, and the OpenAPI document is never sent upstream.

For finite workflow guards, callers may include a `workflow` object with
`history` (trusted prior tool outcomes) and rules of type
`requires_prior_success`. TARKA then rejects a proposed tool call unless the
named prerequisite tool appears earlier in that history with outcome `success`:

```json
{
  "logic_constraints": {
    "workflow": {
      "history": [{"tool": "authorize", "outcome": "success"}],
      "rules": [
        {
          "type": "requires_prior_success",
          "tool": "update_order",
          "prior_tool": "authorize"
        }
      ]
    }
  }
}
```

The history is supplied by the caller and must come from a trusted tool
execution record; this feature does not maintain session state or implement
general LTL. Trace size and number of rules are bounded, and workflow
constraints are stripped before forwarding upstream.

Precompiled rules are available as `finance.discount`,
`supply_chain.allocation`, and `healthcare.consent`; request them by name in a
`logic_constraints.templates` list. Bind every variable required by the chosen
template. The healthcare template checks consent/authorization only and is not
a clinical eligibility policy. Templates are ordinary deterministic rules and
may be combined with caller-provided rules.

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
The authenticated `GET /dashboard/metrics` endpoint returns process-local
aggregate outcomes, bounded-window request latency, and upstream-reported token
totals. It retains no prompts, plans, rules, or per-request diagnostics; all
metrics reset on process restart. Latency includes upstream time and is not the
proxy-overhead SLA measurement.
The static `GET /dashboard` page reads these metrics after the operator enters
the proxy key; it does not persist the key in browser storage.
For a single-process tenant-key deployment, configure
`TARKA_TENANT_API_KEYS` as a JSON object mapping tenant IDs to unique keys of
at least 32 characters, and leave `TARKA_PROXY_API_KEY` unset. Each key sees
only that tenant's in-memory dashboard aggregates. Store this JSON in a secret
manager; do not put it in source control or command history. The current
Terraform example still provisions one proxy key.

The proxy also exposes a non-streaming Anthropic Messages-compatible route at
`POST /v1/messages`. It accepts `x-api-key` and `anthropic-version`, translates
text and tool-use/tool-result messages into OpenAI-compatible upstream
completions, and translates text/tool-call responses back. Image and other
unsupported content blocks are rejected rather than silently discarded.

## AWS deployment

The Terraform baseline is private and requires an approved private DNS zone,
matching ACM certificate, allowed client CIDRs, and secured S3 state bucket.
See the [Terraform runbook](infra/terraform/README.md) for bootstrap, image
push, apply, and destroy steps. No AWS resources have been applied.
