# Operational SLO

The production lifecycle maintains a privacy-safe longitudinal outcome stream for production attempts. It is deliberately separate from the successful-release production metrics ring because a store that only observes successful releases cannot truthfully measure rejection or block rates.

The state is stored privately in Cloudflare KV under `<production>.operational-slo-v1`. The suffix is the SLO storage-schema version, not the clash-relay product version. The ring is bounded to the latest 60 attempts and contains aggregate metadata only.

## Measurements

The summary reports:

- qualification rejection count and rate across production attempts;
- qualification rejection counts by typed failure category;
- retry attempts, recoveries, and retry recovery rate;
- Promotion Guard checks, blocks, and block rate;
- lifecycle duration p50, p95, and maximum;
- candidate SHA transitions, candidate changes, and churn rate;
- latest candidate byte-size delta when two adjacent candidate sizes are available.

A qualification category is recovered from `QualificationStageRejected` and its exception cause chain. No exception-message matching is used for SLO classification.

## Privacy boundary

One attempt may contain only:

- epoch;
- outcome enum;
- bounded duration;
- retry attempted/recovered booleans;
- Promotion Guard checked/blocked booleans;
- candidate SHA-256 and byte count when available;
- a typed qualification failure category.

It never stores candidate bytes, proxy/node names, server addresses, subscription URLs, Cloudflare credentials, endpoint URLs, raw diagnostics, stderr, or exception messages. Invalid persisted state resets fail-safely to an empty bounded ring.

## Failure semantics

SLO persistence is operational observability, not a release gate. A failed SLO read/write cannot make an otherwise valid release fail, and it cannot convert a rejected production attempt into success. Conversely, a qualification rejection or Promotion Guard block is recorded best-effort before the original exception is re-raised.

Dry runs do not mutate the production SLO key.

## Scheduler tuning evidence gate

`slo_summary()` includes `scheduler_tuning_evidence`. The gate exists to prevent one-off failures from turning into scheduler policy changes.

A tuning review becomes eligible only when all of these aggregate conditions exist:

- at least 12 longitudinal production attempts;
- at least 4 candidate transitions;
- a complete lifecycle p50/p95/max distribution.

Before that point the status is `insufficient_evidence`. Reaching `eligible_for_review` does not recommend a change and never applies one automatically. `automatic_tuning_allowed` is always false.

The public GitHub production workflow intentionally exposes only whether private SLO persistence succeeded; it does not publish the private KV ring values. A scheduler change therefore requires a separate review of the private aggregate evidence. Missing public evidence must not be replaced with guessed thresholds.

## Tuning policy

Operational SLO collection does not widen qualification thresholds, retry counts, scheduler hysteresis, or Promotion Guard thresholds. Operational policy changes must be justified by longitudinal aggregate evidence rather than one-off failures. In particular:

- a retry adjustment requires sustained typed transient rejection evidence plus measured recovery benefit;
- a Promotion Guard threshold adjustment requires sustained block evidence and separate inventory analysis;
- scheduler changes require latency/stability evidence from the existing aggregate metrics;
- candidate churn should be interpreted alongside release cadence and byte-size deltas, not as a quality score by itself.
