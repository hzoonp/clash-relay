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

## Region-first browsing automatic failover

The internal runtime graph is region-first. Each region has an independent hidden Stable/Reserve pair:

```text
网页自动 (hidden regional fallback)
├── 网页 · 美国 (hidden fallback)
│   ├── __CR_BROWSING_US_STABLE_AUTO
│   └── __CR_BROWSING_US_RESERVE_AUTO
├── 网页 · 新加坡 (hidden fallback)
│   ├── __CR_BROWSING_SG_STABLE_AUTO
│   └── __CR_BROWSING_SG_RESERVE_AUTO
└── ...
```

The canonical automatic order is `US -> SG -> JP -> TW -> KR -> HK -> OTHER`. Automatic selection follows this strict order:

1. preferred-region Stable;
2. same-region Reserve;
3. only when that whole region is unavailable, the next region's Stable;
4. then that next region's Reserve, and so on.

This prevents a healthy browsing session from changing countries merely because another region is temporarily tens of milliseconds faster.

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

## Scheduler history

Historical stability remains subordinate to live qualification. History can demote a current Stable node only within that node's region. A demoted node remains current-qualified and moves to the same region's Reserve tier. History cannot promote a live Reserve or failed node into Stable and cannot move a node into another region.

The privacy boundary is unchanged: persistent history stores anonymous HMAC fingerprints and aggregate stability data, not node names, endpoints, credentials, subscription URLs, or traffic records.

## Regression gates

The release is blocked unless all of the following hold:

1. canonical public groups do not directly expose proxy providers;
2. `网页浏览` contains `网页自动`, qualified regional choices in policy order, and `DIRECT`, with no raw runtime nodes;
3. `网页自动` is a hidden regional fallback in `routing.browsing.preferred_regions` order;
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
