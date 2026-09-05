# Operational SLO

P51 adds a privacy-safe longitudinal outcome stream for production attempts. It is deliberately separate from the successful-release production metrics ring because a store that only observes successful releases cannot truthfully measure rejection or block rates.

The state is stored privately in Cloudflare KV under `<production>.operational-slo-v1`. It is bounded to the latest 60 attempts and contains aggregate metadata only.

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

## Tuning policy

P51 does not widen qualification thresholds, retry counts, scheduler hysteresis, or Promotion Guard thresholds. Operational policy changes must be justified by longitudinal aggregate evidence rather than one-off failures. In particular:

- a retry adjustment requires sustained typed transient rejection evidence plus measured recovery benefit;
- a Promotion Guard threshold adjustment requires sustained block evidence and separate inventory analysis;
- scheduler changes require latency/stability evidence from the existing aggregate metrics;
- candidate churn should be interpreted alongside release cadence and byte-size deltas, not as a quality score by itself.
