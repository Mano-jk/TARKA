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
- [x] Local p95: 100-variable verifier approximately 15.6 ms; proxy with mock upstream 3.0 ms (not a live-network measurement).
- [x] Terraform formats and validates locally with the S3 backend disabled.

## Pending environment or release verification

- [ ] Confirm CI on `ubuntu-24.04` after pushing the branch.
- [ ] Build and run the Docker image; Docker is not installed on the current workstation.
- [ ] Measure solver and proxy p95 latency on the approved reference or deployed environment; the local mock benchmark is only an estimate.
- [ ] Confirm AWS account, region, private DNS/certificate, approved client CIDRs, remote-state bucket, and TCP 443 egress policy with the cloud owner.
- [ ] Apply and destroy the Terraform dev environment after those approvals.
- [ ] Verify the deployed `/healthz` and `/v1/chat/completions` endpoints.