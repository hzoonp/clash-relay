# Routing V2 runtime hardening

This document records the `v1.0.1` hardening performed after validating the canonical Routing V2 production baseline at commit `507bba81867052ec5a847b3e2431115a06d41a8c`.

## Baseline and rollback

The hardening work starts from the exact validated production commit above. The existing publication workflow preserves the previous validated production bytes before replacing the Cloudflare KV production value, and the existing rollback workflow remains the recovery path.

No source permission is widened by this release. In particular, `subscription_1` remains browsing/AI-only and keeps the strict `> 2.0x` multiplier rejection rule.

## Public FlClash surface

The canonical visible policy groups remain:

- `代理选择`
- `网页浏览`
- `人工智能`

`网页浏览` is a policy-only selector and contains exactly:

```text
网页浏览
├── 网页自动
└── DIRECT
```

It has no `use` field and no provider filter. Raw runtime nodes such as `[BROWSING:ANY] subscription_x/...` are therefore not direct members of the public browsing selector.

## Browsing automatic failover

The internal runtime graph is:

```text
网页浏览
├── 网页自动 (hidden fallback)
│   ├── __CR_BROWSING_STABLE_AUTO  (hidden url-test)
│   └── __CR_BROWSING_RESERVE_AUTO (hidden url-test)
└── DIRECT
```

A three-attempt browsing qualification classifies nodes as follows:

- 3/3 successful probes: Stable;
- 2/3 successful probes: Reserve;
- fewer than 2/3: removed from the browsing inventory for that publication.

The Stable tier is preferred. If it becomes unavailable at runtime, Mihomo automatically evaluates the Reserve tier. `DIRECT` is not an automatic fallback; it remains an explicit user choice.

## Probe consistency

Pre-publication browsing qualification and the published browsing runtime use the same canonical browsing policy probe. The provider health check, Stable and Reserve `url-test` groups, and the `网页自动` fallback inherit the same HTTPS URL, interval, timeout, lazy setting, and expected status from `policies.yaml`.

## Scheduler history

History can demote a current Stable node from the preferred Stable subset when the configured maturity, freshness, and success thresholds justify it. That node remains current-qualified and moves into Reserve. History cannot promote a live Reserve or failed node into Stable and cannot bypass live qualification.

## Regression gates

The release is blocked unless all of the following hold:

1. canonical public groups do not directly expose proxy providers;
2. `网页浏览` contains exactly `网页自动` and `DIRECT`;
3. `网页自动` is a hidden Stable-to-Reserve fallback;
4. Stable and Reserve use only browsing providers;
5. runtime browsing probes use the canonical HTTPS policy probe;
6. source-use isolation remains valid before and after qualification;
7. unit tests pass on Python 3.11 and 3.12;
8. deterministic fixture generation remains byte-stable;
9. both pinned Mihomo versions accept and start the generated configuration;
10. real-Mihomo integration verifies that raw browsing nodes are absent from the public selector and that an unavailable Stable tier fails over to Reserve;
11. the production workflow re-runs generation, isolation audit, browsing qualification, AI qualification, post-qualification audit, dual-core validation, previous-good preservation, and Cloudflare KV publication.
