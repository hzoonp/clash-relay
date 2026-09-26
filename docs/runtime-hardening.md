# Routing V2 runtime hardening

This document records the `v1.0.1` browsing runtime hardening and the `v1.1.0` Browsing Regional Scheduling extension. Both preserve the source-use and publication boundaries of the validated Routing V2 production baseline.

## Baseline and rollback

The regional work starts from the exact validated `v1.0.1` production commit `1f3742e30d4c306f2dc1dbc6888673da1f6b6a83`. The publication workflow preserves the previous validated production bytes before replacing the Cloudflare KV production value, and the rollback workflow remains the recovery path.

No source permission is widened. In particular, `subscription_1` remains browsing/AI-only and keeps the strict `> 2.0x` multiplier rejection rule.

## Public FlClash surface

The canonical top-level visible policy groups remain:

- `代理选择`
- `网页浏览`
- `人工智能`

`网页浏览` is provider-free. After live qualification it contains `网页自动`, only the regional choices that currently have qualified nodes, and `DIRECT`, for example:

```text
网页浏览
├── 网页自动
├── 网页 · 美国
├── 网页 · 新加坡
├── 网页 · 日本
├── 网页 · 台湾
├── 网页 · 韩国
├── 网页 · 香港
├── 网页 · 其他地区
└── DIRECT
```

It has no `use` field and no provider filter. Raw runtime nodes such as `[BROWSING:US] subscription_x/...` are never direct members of the public selector.

## Client-measured browsing automatic selection

The client compares regions by measured latency. Each region has an independent hidden Stable/Reserve pair:

```text
网页自动 (hidden regional url-test, tolerance: 150)
├── 网页 · 美国 (hidden fallback)
│   ├── __CR_BROWSING_US_STABLE_AUTO
│   └── __CR_BROWSING_US_RESERVE_AUTO
├── 网页 · 新加坡 (hidden fallback)
│   ├── __CR_BROWSING_SG_STABLE_AUTO
│   └── __CR_BROWSING_SG_RESERVE_AUTO
└── ...
```

The policy order `US -> SG -> JP -> TW -> KR -> HK -> OTHER` is retained for display and initial selection. Automatic selection compares regional latency on the client's network at a 300-second interval, with a 150 ms tolerance. Within each region, Stable remains preferred over Reserve. A sufficiently faster region can replace a healthy current region.

A manual regional choice is pinned to one region. `网页 · 日本`, for example, contains only Japan Stable and Japan Reserve. It never silently crosses to another country. `DIRECT` remains an explicit user choice and is not an automatic fallback.

## Live qualification

A three-attempt browsing qualification classifies nodes as follows:

- 3/3 successful probes: Stable;
- 2/3 successful probes: Reserve;
- fewer than 2/3: removed from the browsing inventory for that publication.

Qualification is applied independently to the generated regional browsing providers. A region with zero qualified nodes is removed from the published browsing graph. Publication remains fail-closed if no browsing region survives.

## Probe and switching consistency

Pre-publication browsing qualification and the published browsing runtime use the same canonical browsing HTTPS probe. Provider health checks and regional Stable/Reserve schedulers inherit the same URL, timeout, lazy setting, and expected status from `policies.yaml`.

`scheduler.browsing.region_switch_interval` controls cross-region re-evaluation and is intentionally no shorter than the node-level browsing probe interval. The canonical value is 300 seconds, while the current browsing probe interval is 180 seconds.

## Qualification data flow and authorities

The qualification chain runs in one direction, and every layer owns exactly one authority:

1. `proxy_host_qualification` (Runner) — resolves every proxy hostname through the candidate's DoH `proxy-server-nameserver` endpoints using RFC 8484 `application/dns-message` wireformat, parsed strictly by QDCOUNT/ANCOUNT (only Answer-section records of the queried type qualify). Authority: *DNS resolvability from a data-center vantage point*. Any resolver returning a public A record resolves the hostname (AAAA is also accepted when the candidate enables DNS IPv6); quarantining requires two **distinct resolver authorities** to agree on a definitive negative, so a single misbehaving resolver — or the same resolver declared twice — can never kill a node; one negative combined with a server failure, malformed response, or transport failure elsewhere is inconclusive and keeps the node. Runner transport failures never quarantine, probe evidence is cached per hostname (never reused across hostnames), and a stage where no resolver returned any DNS response fails closed instead of mass-quarantining. At least two distinct DoH authorities are required; `system` and non-HTTPS entries cannot take part in the runner probe, and the DNS runtime audit rejects duplicate resolver authorities in the declared configuration.
2. `proxy_endpoint_qualification` (Runner) — bounded-retry TCP connect (3 attempts, 1.5s per-connect timeout, 2.0s per-attempt budget, 12 workers). Authority: *TCP reachability from a data-center vantage point*. Success counts attempts in which at least one deduplicated resolved address connected. 3/3 is Robust; 1–2/3 and zero-success `connect_timeout` are Reserve and retained. `dns_failure` is inconclusive and retained without assigning Reserve. Zero-success `connection_refused` and `network_unreachable` quarantine the endpoint; endpoints without public addresses remain quarantined. After service qualification, endpoint Reserve evidence caps nodes out of preferred general automatic pools and browsing Stable pools. General automatic groups use Robust/Reserve fallback children; an empty browsing Stable pool explicitly contains `REJECT` to prevent Mihomo's implicit empty-group DIRECT fallback. UDP-native transports remain owned by Mihomo runtime probes.
3. `browsing qualification` (Runner + real Mihomo cores) — per-node live probing. Authority: *pre-publication node admission measured through real Mihomo*. 3/3 successful probes → Stable, 2/3 → Reserve, below → removed from the browsing inventory.
4. `AI qualification` (Runner + real Mihomo cores) — per-service live admission with cache policy. Authority: *service reachability measured through real Mihomo*.
5. `client runtime URLTest` (user device) — the continuous authority after publication. Authority: *the only layer that observes the actual carrier access network*. Browsing groups switch after one failed probe (`max-failed-times: 1`), regional and other non-AI automatic groups after two; all non-AI probes use the canonical `https://cp.cloudflare.com/generate_204`.
6. `carrier_qualification` (optional self-hosted probes) — the only future authority for China Telecom / Unicom / Mobile quality.

No Runner-layer result may be interpreted as carrier quality; the summary keeps the three reachability authorities separate.

## Node quality tiers

The qualification summary classifies evidence into three tiers per evidence unit (`node_quality_tiers`) and reports per-stage removal provenance (`removed_by_stage`) plus the canonical totals `qualification_removed_unique_nodes` and `qualification_removed_runtime_entries`; no node identities are emitted:

- **robust** — endpoint admitted on every attempt, hostname answered by the configured DoH resolvers, or browsing Stable (3/3). Browsing Stable is preferred within each region; the client compares regional candidates by latency.
- **reserve** — endpoint admitted with 1–2/3 successes or retained after connect timeouts, hostname probes inconclusive because of runner transport trouble, or browsing Reserve (2/3). Reserve provides recovery within its region; the client compares regional latency. Endpoint DNS failure stays separately inconclusive and does not assign a Reserve cap.
- **quarantined** — DNS-proven unresolvable hostnames (two agreeing distinct DoH authorities), TCP endpoints with definitive refusal/unreachable evidence or no public addresses, browsing nodes below the 2/3 threshold, and core-incompatible nodes. Every stage boundary carries aggregate delta accounting (`removed_by_stage`: unique nodes and runtime entries before/after/removed, by source/region/protocol, with the stage failure reason), so removals are attributable to `hostname`, `endpoint`, `browsing`, `ai`, or `service_hardening`. Any source whose entire runtime inventory was removed is flagged under `sources_fully_removed` with `removed_at_stage`, both unique-node and runtime-entry aggregates, and non-empty failure-category reasons — an unexplainable removal and any final-stage deletion fail closed with `ValidationError`. Unique removal uses the before/after full proxy-fingerprint set difference; TCP host:port probe counts are separate `unique_quarantined_endpoints`; runtime removal counts deleted provider entries, while OpenAI client-path clones are reported separately as runtime entries added by `service_hardening`. The Actions summary, production proof, and release manifest publish aggregate provenance under canonical source IDs and AI service evidence. Runtime source labels are parsed only from the generated name prefix, so source-looking text in original node names cannot change attribution. The legacy `removed_nodes` block is kept for compatibility and its per-source dimensions count runtime entries.

## OpenAI systemic probe-environment isolation

OpenAI live qualification carries a three-state evidence contract: `passed`, `failed`, and
`inconclusive` — with two aggregate states reserved for OpenAI (`probe_environment_blocked`,
`systemic_failure_detected`). "Cannot verify" never equals "verified failed".

Two aggregate mechanisms isolate an OpenAI probe-environment blackout:

1. **Bounded deterministic sentinel probing** — before the full sweep, up to three anonymous
   sentinels are selected deterministically (sorted names, round-robin over sorted region
   labels) and probed against the OpenAI critical endpoints plus a connectivity control
   through the same nodes. If every sentinel fails OpenAI with network-dominated outcomes
   while the control succeeds through the same environment, the full sweep is skipped —
   Claude, Gemini, general, and browsing qualifications are unaffected.
2. **Post-probe systemic detector** — re-checks the full sweep with the same aggregate rule
   (no qualified node, a multi-region sample, network-dominated outcomes, control passed).

Under a systemic verdict the qualification writes **nothing** to the cache: no negative
entries, and previously cached passes keep their `checked_epoch` (they age out through the
7200s OpenAI pass TTL naturally). Routing falls back to the fresh pass cache (last known
good); without one, the service is held on its full unverified pool instead of collapsing
to REJECT. The promotion guard distinguishes the three outcomes: a confirmed collapse (no
systemic evidence) blocks exactly as before; an inconclusive verdict with a fresh LKG
passes with `evidence_source=cache` (the client runtime URLTest remains the actual routing
authority); an inconclusive verdict without an LKG blocks with reason
`probe_environment_hold` — the release is held, the previous verified production version is
preserved, and OpenAI runtime routing is never rewritten to REJECT. Per-service aggregate
evidence (`evidence_status`, `live_tested`, `live_passed`, `live_failed`, `inconclusive`,
`cache_pass_hits`, `systemic_failure_detected`, `dominant_failure_category`,
`evidence_source`) is published under `ai.service_evidence` with no node identities.

## Reachability report authorities

The qualification summary reports reachability evidence under three separate authorities so no single number is over-read:

- `global_preflight_reachable` — the GitHub Runner TCP endpoint admission (the aggregate `endpoint_qualification` block). It filters obviously dead TCP endpoints from a data-center vantage point before qualification. It is never a measure of China Telecom / Unicom / Mobile quality.
- `client_runtime_health` — the client-side URLTest contract plus the browsing/AI qualification outcomes measured through real Mihomo probes: canonical `https://cp.cloudflare.com/generate_204`, browsing `max-failed-times: 1`, regional and other non-AI automatic groups `2`.
- `carrier_qualification` — an optional, pluggable data boundary (`src/clash_relay/carrier_qualification.py`). The repository ships no real probes: a CI job or self-hosted probe operator drops an aggregate-only payload into the lifecycle's private `carrier-qualification.json` (or submits `CarrierProbeResult` rows in-process). Accepted shape: `{"schema_version": 1, "collected_at_epoch": <epoch>, "carriers": {"telecom" | "unicom" | "mobile": {"tested", "reachable", "median_latency_ms"}}}` — the timestamp is optional, a payload older than six hours is reported as `stale` (its aggregates are not passed evidence), and validation rejects unknown carriers, unknown fields, and anything identity-bearing. Only the aggregate reduction (per-carrier tested/reachable/median latency) is published. Without an input the report stays `not_configured`.

## Scheduler history

Historical stability remains subordinate to live qualification. History can demote a current Stable node only within that node's region. A demoted node remains current-qualified and moves to the same region's Reserve tier. History cannot promote a live Reserve or failed node into Stable and cannot move a node into another region. A single transient failed run is debounced; demotion requires repeated failures, and recovery back into the preferred set requires a stronger threshold plus a clean current run, so short-term client jitter cannot cause selection churn.

The privacy boundary is unchanged: persistent history stores anonymous HMAC fingerprints and aggregate stability data (success EMA, consecutive failures, cohort latency EMA), not node names, endpoints, credentials, subscription URLs, or traffic records.

## Regression gates

The release is blocked unless all of the following hold:

1. canonical public groups do not directly expose proxy providers;
2. `网页浏览` contains `网页自动`, qualified regional choices in policy order, and `DIRECT`, with no raw runtime nodes;
3. `网页自动` is a hidden cross-region `url-test` with positive switching tolerance;
4. every manual regional group contains only its own Stable and Reserve tiers;
5. every regional Stable/Reserve tier uses only the matching `cr_browsing_<region>` provider;
6. same-region Reserve is evaluated before the next region;
7. an unavailable whole region can fail over to the next region automatically;
8. browsing runtime probes use the canonical HTTPS policy probe;
9. source-use isolation remains valid before and after qualification;
10. `subscription_1` remains browsing/AI-only and the strict greater-than-2x filter remains active;
11. unit tests pass on Python 3.11 and 3.12;
12. deterministic fixture generation remains byte-stable;
13. both pinned Mihomo versions accept and start the generated configuration;
14. real-Mihomo integration verifies same-region Reserve recovery and cross-region fallback semantics;
15. the production workflow re-runs generation, isolation audit, browsing qualification, AI qualification, post-qualification audit, dual-core validation, previous-good preservation, and Cloudflare KV publication.
