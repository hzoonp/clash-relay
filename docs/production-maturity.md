# P18.1-P23 production maturity contract

P18.1-P23 move clash-relay from architecture stabilization to long-term production operation. These phases do not relax the six-scenario routing model, source isolation, ACL4SSR fidelity, client-owned DNS, or fail-closed qualification.

## P18.1 - Documentation contract

Authoritative onboarding documentation describes only the current architecture:

- Mihomo stable/prerelease tags come from `tools/mihomo-versions.json`;
- production uses the versioned Cloudflare KV release transaction;
- rollback revalidates historical bytes against current policy;
- `clash-relay doctor` is the preflight entry point.

`scripts/audit_documentation_contract.py` is a regression guard against known stale publication/version wording.

## P19 - Production observability

Private longitudinal production metrics remain bounded and aggregate-only. Allowed data includes candidate byte hashes/sizes, regional cohort counts, AI service counts, release state, stable-core validation counts, and bounded phase durations.

Forbidden metrics include node names, server addresses, credentials, subscription URLs, raw child-process output, and per-node qualification results. Existing private state is sanitized before re-persistence so unknown historical fields cannot silently become part of the supported metrics contract.

## P21 - Disaster recovery verification

Release transaction tests inject failures at immutable staging, ambiguous writes, production activation, pointer commit, and compensation. A failed transaction must either leave the previous client-visible production bytes intact or report incomplete compensation explicitly.

Versioned rollback verifies the previous immutable config and its exact manifest before current-policy and Mihomo validation. Rollback remains current-policy gated.

## P20 - Scheduler Quality V2

History remains anonymous and can operate only inside the current live-qualified stable set. P20 adds hysteresis and consecutive-failure debounce so one transient failure does not cause unnecessary Stable/Reserve oscillation. A historically demoted node requires a higher recovery threshold before returning to preferred Stable.

Scheduler state v3 persists only anonymous fingerprints and aggregate stability state. v1/v2 state migrates forward without changing node fingerprints. Manual region selection never crosses region; automatic mode retains region-local preference semantics.

## P22 - Performance and network cost

Qualification records aggregate stage durations so optimization is evidence-driven. Production reuses the already downloaded primary stable Mihomo binary for the first stable-matrix validation rather than downloading the same pinned core twice. Every pinned stable core still validates the exact final candidate.

Timings remain report/metrics metadata and never enter generated candidate bytes, preserving deterministic generation.

## P23 - Fork preflight

`clash-relay doctor` validates public declarations, enabled subscription Secret readiness, and the stable Mihomo manifest. Optional `--check-subscriptions` performs the normal bounded subscription fetch policy and reports counts only. Optional `--check-cloudflare` performs Cloudflare KV read connectivity only.

Doctor never publishes bytes and never prints subscription URLs, subscription payloads, Cloudflare credentials, or production configuration bytes. Connectivity errors are reduced to safe public identifiers/status messages. CI executes `doctor --public-only` on every supported Python version.

A fork should fail early with a safe readiness result rather than discovering a missing Secret or invalid Cloudflare setup deep inside a production run.
