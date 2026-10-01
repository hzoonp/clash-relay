# Routing rules and ACL4SSR

Canonical production treats the pinned ACL4SSR Online profile as the classification baseline. clash-relay adds source isolation, live qualification, regional scheduling, and declared AI and download extensions.

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
3. The download-only inbound and downloader process rules run before every baseline rule. Each download classifier emits an adjacent `REJECT` terminal guard for unsupported UDP. Confirmed download domains and `Download.list` run before AI, media, and generic ProxyLite.
4. ACL4SSR raw-node wildcards are adapted to source-aware scenario selectors. Raw nodes are not copied directly into public selectors because doing so would break multi-subscription source isolation.

Any additional classification source or compatibility change must be declared in `rules/acl4ssr.yaml` and pass the parity gate.

## Canonical rule order

| Order | Source | Production target |
| ---: | --- | --- |
| 1 | `IN-NAME,download-in` | `下载流量` |
| 2 | declared downloader / Play processes | `下载流量` |
| paired | each download classifier's terminal guard | `REJECT` |
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
| 101 | confirmed Play delivery hosts | `下载流量` |
| 102 | confirmed release asset hosts | `下载流量` |
| 103 | `Download` | `下载流量` extension |
| 105 | `AI` | `人工智能` extension |
| 106 | `OpenAi` | `人工智能` extension |
| 110 | `ProxyMedia` | `流媒体` |
| 120 | `ProxyLite` | `网页通用自动` (General only) |
| 130 | `ChinaDomain` | `全球直连` |
| 140 | `ChinaCompanyIp` | `全球直连` |
| 150 | `GEOIP,CN` | `全球直连` |
| final | `MATCH` | `漏网之鱼` |

The old canonical `ProxyGFWlist` substitution and standalone YouTube/Netflix/game/Bilibili/ChinaMedia classification graph are deliberately removed. ACL4SSR Online decides the classification category; clash-relay decides which source-safe node inventory that category may use.

## Node admission before routing

Untrusted subscription entries pass through admission before they can reach classification or any
runtime inventory:

```text
parse and structural validation
  -> high-confidence informational pseudo-node rejection
  -> subscription deny_name_patterns
  -> multiplier ceiling
  -> country/capability classification
  -> deduplication
```

The informational stage rejects status/support labels such as `剩余流量`, `套餐到期`,
`距离重置`, `官方网站`, `联系客服`, and explicit English status labels such as
`Traffic:`, `Expire:`, `Remaining:`, or `Reset:`. It deliberately does not reject broad
tokens such as `流量`, `套餐`, `官网`, or `节点` on their own, so ordinary endpoint names
such as `日本流量优化 02` remain valid.

Diagnostics are aggregate-only. Rejected informational node names are never copied into the build
report.

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

subscription_2
  allowed_uses: general, browsing

subscription_3
  allowed_uses: general, browsing

subscription_4
  allowed_uses: general, browsing

subscription_5
  allowed_uses: general, browsing
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

Generic `ProxyLite` traffic uses hidden `网页通用自动`, whose regional scheduler is derived directly from the General inventory. It cannot inherit the manual state of `代理选择` or reach SUB_1. Its provider nodes undergo the browsing probe and qualification, regional Stable/Reserve filtering, history preference, and client URLTest with the same 300-second region switch and 150 ms tolerance as the controlled browsing route. The separate `网页浏览` selector remains available for explicitly controlled browsing traffic and may use SUB_1.

The compiled web route shares existing `cr_general_*` providers from the start. Exact regional and qualified-node filters define its Stable/Reserve candidates; pruning this web route never removes nodes from the shared General/download inventory. No `cr_web_general_*` provider or separate web-general inventory is generated. Qualification probes identical full proxy configurations once per source, then applies the result to their runtime aliases; different credentials, dialers, or subscription sources are never merged. Production Proof requires passed General source isolation, regional scheduling, and web qualification evidence, with final membership matching the qualification count.

Manual regional choices stay pinned to their selected region. History demotion remains region-local and does not remove a currently qualified node from Reserve eligibility.

Browsing Stable, Reserve, regional fallback, and the browsing auto group use `max-failed-times: 1` so one failed local connection starts health re-evaluation promptly. Other non-AI automatic groups keep a threshold of 2; AI runtime health checks remain owned by their existing qualification/client-path contract.

General-pool country `url-test` groups declare `on_empty: omit`. The compiler counts leaves after the group filter against the resolved general inventory, omits a region with no matching general node, and removes that group from every upper selector. Browsing groups continue to use the separate browsing inventory, so a subscription-1-only country remains available under `网页浏览` without entering general, media, messaging, or downloads. All automatic groups are checked again against their final provider/group expansion before serialization.

## Media, messaging, and download

`ProxyMedia -> 流媒体`, `Telegram -> 消息通讯`, and `Download -> 下载流量` all use general-only schedulers. They cannot select subscription 1.

### Download isolation guarantee

The generated config binds a local mixed listener named `download-in` to `127.0.0.1:7891` with `proxy: 下载流量`. This listener binding sends traffic to the download selector independently of ordinary rule matching, including if the runtime mode changes to Global. `IN-NAME` remains as a checked compatibility rule. Every download process, domain, and ACL4SSR Download classifier also emits an adjacent `REJECT` guard: if Mihomo skips its primary route because the selected adapter cannot carry UDP, the same classifier terminates before AI or browsing rules. The download selector, its automatic general pool, and every manual regional choice are audited on the final qualified RuntimeGraph, including provider and dialer-proxy paths. `doctor --public-only` checks declarations; production audit checks emitted rules and the graph.

This guarantees isolation for connections that enter a download path. A browser or AI app may download a file over a connection indistinguishable from normal web traffic. A deployment may claim **all downloads avoid SUB_1** only after it verifies that its clients hand off every download to the dedicated listener or a verified downloader, or prevents those clients from using SUB_1. An enabled listener alone does not establish that client behavior. Production proof records the configuration guarantee separately from deployment verification.

Follow [Download isolation client acceptance](download-isolation-acceptance.md) on each target device before asserting the deployment guarantee.

The project-maintained domain list is deliberately narrow: `gvt1.com`, `gvt2.com`, and `release-assets.githubusercontent.com`. Ordinary `google.com`, `github.com`, and generic CDN hosts are not treated as confirmed downloads; if they match ProxyLite or final fallback they use General, not SUB_1. Domestic DIRECT or other earlier general-only matches still cannot reach SUB_1.

Media service capability checks may influence node scheduling inside `流媒体`, but they must not redefine the ACL4SSR Online classification order. In particular, canonical routing no longer inserts standalone YouTube or Netflix rules ahead of `ProxyMedia`.

## AI live qualification

AI remains a protected clash-relay extension. Candidate nodes are qualified independently for OpenAI, Claude, and Gemini behind the `ServiceQualification` registry. Hong Kong and the United Kingdom are excluded before qualification, and each service follows its own qualified set in the declared `US -> SG -> JP -> TW -> KR -> OTHER` preference order.

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
