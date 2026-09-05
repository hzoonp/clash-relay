# Routing rules and ACL4SSR

Canonical production treats the pinned ACL4SSR Online profile as the classification source of truth. clash-relay adds source isolation, live qualification, regional scheduling, and exactly two classification extensions: AI and downloads.

## Canonical ACL4SSR reference

`rules/acl4ssr.yaml` pins:

```text
repository: ACL4SSR/ACL4SSR
ref: c498ae4911f15b19c5ceaef6f8737ca8705b4430
license: CC-BY-SA-4.0
reference: Clash/config/ACL4SSR_Online.ini
```

`rules/acl4ssr-online.reference.ini` vendors that immutable Online profile. `scripts/audit_acl4ssr_fidelity.py` verifies that the vendored copy still matches the pinned upstream file and that the canonical manifest preserves baseline ruleset order, baseline targets, and compatibility-selector defaults.

The final private profile still embeds selected ACL4SSR fragments as inline Mihomo rule providers, so clients do not depend on GitHub or ACL4SSR at runtime.

## Intentional deviations

Only the following canonical deviations are allowed:

1. `BanProgramAD.list` / `应用净化` is disabled because it caused confirmed mobile image/CDN failures. `BanAD.list` remains enabled.
2. AI/OpenAI rules run before `ProxyMedia.list` so protected AI traffic reaches service-specific qualification instead of being swallowed by the broad media list.
3. `Download.list` runs before `ProxyLite.list` so known download traffic reaches `下载流量` instead of generic browsing.
4. ACL4SSR raw-node wildcards are adapted to source-aware scenario selectors. Raw nodes are not copied directly into public selectors because doing so would break multi-subscription source isolation.

Any additional classification source or compatibility change must be declared in `rules/acl4ssr.yaml` and pass the parity gate.

## Canonical rule order

| Order | Source | Production target |
| ---: | --- | --- |
| 10 | `LocalAreaNetwork` | `全球直连` |
| 20 | `UnBan` | `全球直连` |
| 30 | `BanAD` | `广告拦截` |
| disabled | `BanProgramAD` | intentionally disabled |
| 50 | `GoogleFCM` | `谷歌FCM` |
| 60 | `GoogleCN` | `全球直连` |
| 70 | `SteamCN` | `全球直连` |
| 80 | `Microsoft` | `微软服务` |
| 90 | `Apple` | `苹果服务` |
| 100 | `Telegram` | `消息通讯` |
| 105 | `AI` | `人工智能` extension |
| 106 | `OpenAi` | `人工智能` extension |
| 110 | `ProxyMedia` | `流媒体` |
| 115 | `Download` | `下载流量` extension |
| 120 | `ProxyLite` | `网页浏览` |
| 130 | `ChinaDomain` | `全球直连` |
| 140 | `ChinaCompanyIp` | `全球直连` |
| 150 | `GEOIP,CN` | `全球直连` |
| final | `MATCH` | `漏网之鱼` |

The old canonical `ProxyGFWlist` substitution and standalone YouTube/Netflix/game/Bilibili/ChinaMedia classification graph are deliberately removed. ACL4SSR Online decides the classification category; clash-relay decides which source-safe node inventory that category may use.

## Six public controls

FlClash exposes only the main scenario decisions:

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

`流媒体`, `消息通讯`, and `下载流量` are provider-free selectors whose defaults are the hidden `媒体自动`, `通讯自动`, and `下载自动` general-only schedulers. The familiar ACL4SSR compatibility selectors remain hidden:

```text
全球直连: DIRECT -> 代理选择 -> 自动选择
广告拦截: REJECT -> DIRECT
谷歌FCM: 代理选择 -> 全球直连 -> 自动选择
微软服务: 全球直连 -> 代理选择
苹果服务: 代理选择 -> 全球直连
漏网之鱼: 代理选择 -> 全球直连 -> 自动选择
```

These member orders are parity-checked against the pinned Online profile.

## Source-policy boundary

Routing category and node inventory are independent:

```text
subscription_1
  allowed_uses: browsing, ai
  EMBY-labelled nodes: excluded
  max_node_multiplier: 2.0

subscription_2+
  allowed_uses: general, browsing, ai
```

The selector checks `allowed_uses` before provider generation. Therefore subscription 1 is structurally absent from the general inventory used by media, messaging, downloads, final fallback, and compatibility selectors.

ACL4SSR's single-subscription `.*` node wildcard is not reproduced literally. The canonical adaptation is:

```text
ACL4SSR classification
        ↓
scenario selector
        ↓
source_use inventory
        ↓
qualified/scheduled nodes
```

This keeps ACL4SSR classification fidelity without weakening source permissions.

## Browsing scheduling

`ProxyLite -> 网页浏览` uses the browsing inventory and preserves the canonical regional order:

```text
网页自动
  US Stable -> US Reserve
  -> SG Stable -> SG Reserve
  -> JP -> TW -> KR -> HK -> OTHER
```

Manual regional choices stay pinned to their selected region. History demotion remains region-local and does not remove a currently qualified node from Reserve eligibility.

## Media, messaging, and download

`ProxyMedia -> 流媒体`, `Telegram -> 消息通讯`, and `Download -> 下载流量` all use general-only schedulers. They cannot select subscription 1.

Media service capability checks may influence node scheduling inside `流媒体`, but they must not redefine the ACL4SSR Online classification order. In particular, canonical routing no longer inserts standalone YouTube or Netflix rules ahead of `ProxyMedia`.

## AI live qualification

AI remains a protected clash-relay extension. Candidate nodes are qualified independently for OpenAI, Claude, and Gemini behind the `ServiceQualification` registry. Hong Kong is excluded before qualification, and each service follows its own qualified set in the declared `US -> SG -> JP -> TW -> KR -> OTHER` preference order.

A protected service with no qualified node fails closed to `REJECT`; if protected AI qualification cannot satisfy the production contract, publication aborts and the previous KV value remains untouched.

## Multiplier and EMBY admission

Subscription 1 admission happens before classification and deduplication:

```text
2x       accepted
2.01x    rejected
3倍      rejected
unmarked accepted
EMBY     rejected from subscription_1
```

The multiplier filter reacts only to explicit markers and does not infer an unmarked commercial billing ratio.

## Validation contract

Canonical routing changes must pass:

- schema validation;
- pinned ACL4SSR Online upstream/vendored parity;
- baseline ruleset order/target parity;
- explicit intentional-deviation checks;
- source-use and end-to-end reachability audits;
- subscription 1 EMBY and >2x admission tests;
- six-group public-surface and provider-leakage tests;
- browsing regional scheduling regression tests;
- independent service qualification tests;
- Ruff and Python 3.11/3.12/3.13 quality gates;
- deterministic fictional generation;
- Routing V2 Drift Guard;
- every stable Mihomo core declared in `tools/mihomo-versions.json`, including startup/provider integration;
- post-qualification production re-audit before Cloudflare KV publication.
