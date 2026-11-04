# Phase 1 Acceptance Checklist

## Verified locally

- [x] A 30% discount against a 20% cap returns `UNSAT` with both values in the diagnostic.
- [x] A discount at the 20% cap returns `SAT`.
- [x] Numeric comparisons, arithmetic, `AND`, `OR`, `NOT`, and parentheses are supported.
- [x] Unsupported rule syntax, missing bindings, and division by zero are rejected.
- [x] Rule length and expression complexity are bounded; unsupported modulo is rejected.
- [x] The Gateway retries a rejected response and returns a corrected valid response.
- [x] Tool-call arguments can supply the structured plan.
- [x] Undeclared and multi-tool calls fail closed.
- [x] Exhausted retries fail closed by default; `fail_open` is configurable.
- [x] Fixed bindings preserve JSON types, retry results do not leak across attempts, and oversized/malformed upstream responses fail closed.
- [x] The proxy requires a configured bearer key, limits request size, exposes `/healthz`, strips `logic_constraints`, and returns structured errors.
- [x] Constrained streaming requests return a clear 400 error.
- [x] `uv sync --locked`, Ruff, ty, and pytest pass on Windows with Python 3.15.2.
- [x] Local p95 (2026-11-04): 100-variable verifier 16.8 ms; proxy with mock upstream 3.6 ms (not a live-network measurement).
- [x] Terraform formats and validates locally with the S3 backend disabled.

## Pending environment or release verification

- [ ] Confirm CI on `ubuntu-24.04` after pushing the branch.
- [ ] Build and run the Docker image; Docker is not installed on the current workstation.
- [ ] Measure solver and proxy p95 latency on the approved reference or deployed environment; the local mock benchmark is only an estimate.
- [ ] Confirm AWS account, region, private DNS/certificate, approved client CIDRs, remote-state bucket, and TCP 443 egress policy with the cloud owner.
- [ ] Apply and destroy the Terraform dev environment after those approvals.
- [ ] Verify the deployed `/healthz` and `/v1/chat/completions` endpoints.

## Full SRD progress

The items below are separate from Phase 1 acceptance and are not represented as
complete just because the local Phase 1 checks pass.

- [x] JSON Schema plan validation and OpenAPI 3.1 JSON request-body schema ingestion.
- [x] Built-in finance discount, supply allocation, and consent-only rule templates.
- [x] Finite-trace `requires_prior_success` tool-call guard with caller-supplied trusted history.
- [x] Anthropic Messages text/tool-use subset and authenticated OpenAI-compatible proxy.
- [x] Typed TypeScript proxy client, build/test script, and CI job.
- [x] Static dashboard and authenticated process-local aggregate metrics, partitioned by configured tenant key.
- [ ] Fine-tuned SLM autoformalizer, training/evaluation dataset, and model serving. This requires an approved model/runtime, dataset policy, and compute/provider choice.
- [ ] General execution-DAG and LTL/state-machine verification with trusted server-side session state. Current workflow guard is deliberately limited and does not persist or authenticate execution history independently.
- [ ] OPA/Rego ingestion, complete JSON Schema/OpenAPI coverage, and precompiled domain templates beyond the current narrow examples.
- [ ] Full Anthropic compatibility, including streaming, image/multimodal content, and provider-specific options.
- [ ] Durable, production dashboard analytics and detailed UNSAT diagnostics. Current telemetry is process-local aggregates and intentionally does not retain policy text or request content.
- [ ] Isolated solver sandboxes and production tenant isolation review. Tenant keys isolate auth and metrics, but do not yet provide per-tenant process/resource sandboxes.
- [ ] Production latency/SLO and enforcement-accuracy evidence, container execution, approved AWS apply/destroy, and deployed endpoint verification.