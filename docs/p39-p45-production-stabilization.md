# P39-P45 production stabilization

P39-P45 is the v1.8.1 reliability boundary after the P27-P38 architecture consolidation. It intentionally adds no new public routing scenario and does not replace the proven versioned Cloudflare KV release transaction.

## P39 — Production Qualification Reliability 2.0

Qualification failures are now classified by a structured child-stage contract instead of exception-message matching:

```text
transient
policy_rejection
core_rejection
configuration
process_error
protocol_error
```

Only a whole browsing live probe with zero successful samples and exclusively bounded transient infrastructure outcomes (`probe_error`, missing delay, HTTP 429, or HTTP 5xx) may retry once. The second attempt is rebuilt from the immutable generated candidate. Partial success, provider depletion, transport admission failure, Mihomo/core rejection, configuration errors, and unstructured protocol errors remain non-retryable and fail closed.

## P40 — Release / Rollback Reliability

The application layer now records release progress explicitly:

```text
prepared -> qualified -> promoted -> published -> verified
```

Dry runs use `prepared -> qualified -> promoted -> verified`.

The existing immutable release transaction remains the publication mechanism. Fault-injection coverage freezes these behaviors:

- pre-activation timeout / HTTP 429 / HTTP 5xx leaves the exact current production bytes active;
- an ambiguous successful write is resolved only by exact read-back;
- pointer-commit failure invokes compensating restoration;
- incomplete compensation is surfaced explicitly;
- rollback rehearsal resolves and reactivates the exact previous immutable release, reversing current/previous pointers without deleting either release manifest.

Once client-visible publication has committed, optional proof/manifest/state/metrics failures are post-release observability degradation. They do not turn a committed release into a fictional pre-publication failure. A release reaches `verified` only after proof and release manifest complete.

## P41 — Production Observability v2

Production metrics are no longer a hidden side effect of scheduler-history persistence. `ProductionPipeline` explicitly invokes `publish_production_metrics.py` as an independent best-effort post-release stage.

The private bounded ring remains state version 1 for backward compatibility and stores at most 30 sanitized runs. New safe fields include:

- qualification stage-attempt count;
- retry recovery status and typed recovered category;
- Promotion Guard status and aggregate violation count;
- release phase/history;
- bounded lifecycle-stage timings;
- retry-run and retry-recovery aggregate counts.

Metrics still exclude node names, servers, ports, credentials, subscription URLs, Cloudflare credentials, endpoint payloads, child stderr, and generated configuration bytes.

## P42 — Policy Model v2 Finalization

Canonical `policies.yaml` remains the v2 manifest with exactly four domain fragments:

```text
routing        -> routing
scheduling     -> scheduler, probes
classification -> capabilities, cost_levels, country_classification
topology       -> pools, chains
```

Known sections in the wrong domain fail closed during `PolicyDocument` composition. Physical Policy Model v1 remains readable as a migration input but is explicitly reported as `deprecated`. Production canonical policy is v2/current.

## P43 — Chaos / Failure Matrix

Executable failure coverage now includes:

- source timeout/network failure with secret redaction;
- HTML/error subscription payload rejection;
- empty subscription producing zero inventory;
- typed live-probe transient retry and non-retryable policy/core/protocol failures;
- Cloudflare pre-activation 429/5xx/timeout;
- ambiguous writes and compensation;
- corrupt aggregate metrics recovery;
- serialized publish Actions with `cancel-in-progress: false`;
- post-commit observability isolation.

The invariant is: bad candidates are never published; optional derived state cannot expand routing permissions; public diagnostics do not reveal private runtime data; and every rejection identifies a safe stage/category when a structured child protocol is available.

## P44 — Public Fork UX

`clash-relay doctor --public-only` now reports the canonical Policy Model version/status, enabled subscription Secret names, stable Mihomo-core count, scheduler declaration status, and actionable first-run guidance. It never reads or prints Secret values.

The supported first-publication path is:

```text
Fork
  -> configure CLASH_RELAY_SUBSCRIPTIONS
  -> configure Cloudflare KV settings
  -> doctor --public-only
  -> private doctor
  -> manual workflow publish=false
  -> inspect aggregate proof
  -> intentional publish=true
```

English and Simplified Chinese quickstarts describe the same doctor-first path.

## P45 — v1.8.1 stabilization release

v1.8.1 freezes the P39-P45 production-reliability boundary while retaining:

- Python 3.11 / 3.12 / 3.13 quality coverage;
- deterministic fictional generation;
- Routing V2 Drift Guard;
- repository/documentation/architecture audits;
- every pinned stable Mihomo core and real startup/provider integration;
- fixed six public FlClash scenario groups;
- `subscription_1` browsing/AI-only admission and strict >2x rejection;
- source-only GitHub Releases and private Cloudflare production bytes.

No `main` merge or production publication is part of the implementation boundary itself. Those remain separate explicit operator actions after PR validation is fully green.
