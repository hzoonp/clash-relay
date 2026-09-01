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

Forbidden metrics include node names, server addresses, credentials, subscription URLs, raw child-process output, and per-node qualification results.

## P21 - Disaster recovery verification

Release transaction tests inject failures at immutable staging, ambiguous writes, production activation, pointer commit, and compensation. A failed transaction must either leave the previous client-visible production bytes intact or report incomplete compensation explicitly.

Rollback remains current-policy gated.

## P20 - Scheduler Quality V2

History remains anonymous and can operate only inside the current live-qualified stable set. P20 adds hysteresis and consecutive-failure debounce so one transient failure does not cause unnecessary Stable/Reserve oscillation. A historically demoted node requires a higher recovery threshold before returning to preferred Stable.

Manual region selection never crosses region; automatic mode retains region-local preference semantics.

## P22 - Performance and network cost

Qualification records aggregate stage durations so optimization is evidence-driven. Production reuses the already downloaded primary stable Mihomo binary for the first stable-matrix validation rather than downloading the same pinned core twice. Exact candidate validation remains unchanged.

## P23 - Fork preflight

`clash-relay doctor` validates public declarations, enabled subscription Secret readiness, the Mihomo manifest, and optional Cloudflare read connectivity. It never publishes bytes and never prints secret values.

A fork should fail early with a safe readiness result rather than discovering a missing Secret or invalid Cloudflare setup deep inside a production run.
