# Declarative scheduler controls

`policies.yaml` may declare the production scheduler without changing Python code. The block is optional for backward compatibility; when omitted, the v0.1.0 defaults remain active. When `scheduler:` is present, its declared values are authoritative for the supported knobs below.

```yaml
scheduler:
  browsing:
    attempts: 3
    reserve_successes: 2
  history:
    min_runs: 2
    min_success_ema: 0.8
    max_age_seconds: 2592000
  ai_cache:
    pass_ttl_seconds: 21600
    failure_ttl_seconds: 3600
```

## Browsing

`attempts` controls how many live HTTPS group-delay samples are taken during the production gate. A node that succeeds in every attempt is the current live-stable tier. `reserve_successes` is the minimum number of successes required to remain in the browsing provider for manual/reserve use.

The canonical values preserve the existing behavior:

- 3/3: stable automatic candidate.
- 2/3: qualified reserve/manual candidate.
- fewer than 2/3: rejected from browsing.

Changing these values never changes source permissions. A source admitted only for `browsing` and `ai` remains unreachable from `general` routes.

## Historical browsing preference

History is applied only after current live qualification. `min_runs`, `min_success_ema`, and `max_age_seconds` decide whether a currently stable node has enough recent evidence to stay in the preferred automatic subset.

History cannot promote a current reserve or failed node. Stale history is ignored rather than used to punish a node indefinitely.

## AI qualification cache

`pass_ttl_seconds` controls how long a successful service-node result may be reused. `failure_ttl_seconds` controls failed results and may not exceed the pass TTL. The canonical failure TTL is deliberately shorter so previously unavailable nodes are re-tested sooner.

The cache is an optimization, not an admission authority. New, changed, expired, or unavailable cache entries are live-qualified with the existing service-specific probes. The full proxy payload is HMAC-fingerprinted privately, so a protocol, endpoint, port, credential, or provider change invalidates reuse automatically.

## Safe ranges

The schema and runtime loader enforce conservative bounds:

- browsing attempts: 1–10;
- reserve successes: 1–attempts;
- history minimum runs: 1–100;
- history minimum success EMA: 0–1;
- history maximum age: 1 hour–90 days;
- AI cache pass TTL: 60 seconds–24 hours;
- AI cache failure TTL: 60 seconds–pass TTL.

For a public Fork, start with the canonical block above and change one dimension at a time. The production proof remains aggregate-only and the route reachability audit remains the final source-permission boundary.
