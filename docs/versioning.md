# Versioning and compatibility

`clash-relay` follows Semantic Versioning. `v0.1.0` established the first production baseline, `v1.0.0` froze the public compatibility contract, `v1.0.1` hardened browsing failover, `v1.1.0` added browsing-region scheduling, and `v1.2.0` restores ACL4SSR Online classification fidelity while preserving the established source-permission boundary.

## Compatibility contract

A patch release may fix bugs, harden validation, improve diagnostics, tune privacy-safe implementation details, or update supported dependency/core pins without changing documented source-use and routing semantics.

A minor release may add a compatible scenario surface, explicitly declared classifier extensions, probes, regional scheduling dimensions, safe observability, or state behavior while preserving existing security boundaries.

A major release is required when a documented source permission, configuration field meaning, persistent-state migration path, security boundary, or public deployment contract changes incompatibly.

## Canonical v1.2 invariants

- A source declaring `allowed_uses: [browsing, ai]` cannot become reachable from a `general`, media, messaging, download, or final route, even through nested groups, fallback groups, provider indirection, or `dialer-proxy`.
- The canonical first subscription rejects EMBY-labelled nodes and only explicit multipliers strictly greater than `2.0`; exactly `2.0` and unmarked nodes remain eligible.
- The pinned `ACL4SSR_Online.ini` profile is the canonical baseline for classification source order, baseline targets, and compatibility-selector default member order.
- `ProxyLite.list` is the canonical generic foreign-web classifier and targets `网页浏览`. `ProxyGFWlist` is not the v1.2 canonical browser classifier.
- `ProxyMedia.list` is the canonical generic foreign-media classifier and targets `流媒体`; Telegram targets `消息通讯`.
- AI/OpenAI before `ProxyMedia` and `Download.list` before `ProxyLite` are explicit clash-relay classifier extensions. Undeclared classification extensions fail the fidelity gate.
- `BanProgramAD.list` / `应用净化` is intentionally disabled because of confirmed mobile image/CDN breakage. Basic `BanAD.list` remains enabled.
- ACL4SSR raw-node wildcards are not copied into public scenario selectors. Source-aware inventories replace that single-subscription behavior so source isolation remains enforceable.
- Final `MATCH` remains `漏网之鱼` on the general graph.
- The canonical top-level user-facing groups are exactly `代理选择`, `网页浏览`, `人工智能`, `流媒体`, `消息通讯`, and `下载流量`. These public scenario groups do not attach proxy providers directly.
- `流媒体`, `消息通讯`, and `下载流量` are general-only public selectors. Their automatic defaults are hidden `媒体自动`, `通讯自动`, and `下载自动` schedulers followed by general regional helpers and `DIRECT`; browsing/AI-only sources cannot become reachable through them.
- Hidden ACL4SSR compatibility selectors preserve the pinned Online defaults: `全球直连 = DIRECT -> 代理选择 -> 自动选择`, `广告拦截 = REJECT -> DIRECT`, `谷歌FCM = 代理选择 -> 全球直连 -> 自动选择`, `微软服务 = 全球直连 -> 代理选择`, `苹果服务 = 代理选择 -> 全球直连`, and `漏网之鱼 = 代理选择 -> 全球直连 -> 自动选择`.
- Browsing owns an independent regional order `US -> SG -> JP -> TW -> KR -> HK -> OTHER`; changing browsing preference does not change AI service preference order.
- `网页浏览` contains `网页自动`, every currently available fixed-region browsing choice in preference order, and `DIRECT`. Raw `[BROWSING:*]` runtime nodes are never direct members of the public selector.
- Browsing qualification is live and fail-closed. Canonical policy is three attempts: 3/3 is Stable, 2/3 is Reserve, and fewer than 2/3 is rejected for that publication.
- Automatic browsing routing is region-first: preferred-region Stable, then same-region Reserve, then the next available region. Lower instantaneous delay elsewhere is not sufficient reason to switch regions.
- A manual regional browsing choice never crosses regions. It may recover from Stable to same-region Reserve, but another country's nodes are not valid members of that group.
- Completely unavailable browsing regions may be omitted after live qualification, but publication fails if no browsing region retains a qualified node.
- Browsing pre-publication qualification, provider health checks, regional Stable/Reserve schedulers, and cross-region fallback use the canonical HTTPS probe semantics. `scheduler.browsing.region_switch_interval` controls cross-region re-evaluation and cannot be shorter than the browsing probe interval.
- Historical scheduler state may narrow the preferred current Stable subset. A historically demoted but currently qualified node moves to Reserve in the same region instead of disappearing from automatic failover eligibility. History never promotes a current Reserve or live-failed node into Stable.
- OpenAI, Claude, and Gemini qualify independently and fail closed per service. Hong Kong is excluded before qualification. The private AI cache is payload-sensitive and TTL-bounded and falls back to live probing when reuse is unsafe.
- The exact production candidate is validated with Mihomo v1.19.30 and v1.19.29 before publication.
- A failed ACL fidelity, source reachability, qualification, core-validation, or publication gate does not intentionally replace the last validated production value.
- Manual rollback requires explicit confirmation and revalidates previous-good bytes with both supported Mihomo cores before activation.
- Production configuration, previous-good bytes, scheduler state, AI cache, production metrics, subscription responses, and node-level qualification results are private operational data and are not attached to GitHub Releases.
- Production subscription fetching defaults to HTTPS and rejects URL userinfo, private/special-use IP literals, localhost names, and DNS resolutions containing private/special-use IPv4 or IPv6 addresses.

## ACL4SSR fidelity compatibility

The canonical repository vendors the immutable Online reference under `rules/acl4ssr-online.reference.ini`. CI and release workflows fetch the same upstream ref and require byte-equivalent normalized content before validating the manifest against it.

The fidelity report may contain only declared deviations and extensions. Adding a new upstream source replacement, moving a baseline source across another baseline source, changing a compatibility selector default, or silently re-enabling `BanProgramAD` is treated as drift and fails closed.

This contract separates responsibilities:

```text
ACL4SSR Online
  -> classification categories and baseline order

clash-relay
  -> source admission
  -> source-aware inventories
  -> AI / Download declared extensions
  -> live qualification
  -> regional scheduling
  -> validation and private publication
```

## Persistent state compatibility

Persistent state is auxiliary and must degrade safely when missing or invalid.

- Browsing scheduler state remains privacy-preserving and keyed by private HMAC fingerprints. Regional scheduling applies historical demotion separately inside each region; persistent node records do not contain node names, endpoints, credentials, or subscription URLs.
- AI qualification cache v1 is keyed by a private HMAC of provider identity plus the full proxy payload, so payload changes automatically invalidate reuse.
- Production metrics v1 is aggregate-only and retained as a bounded ring.
- A future incompatible state format must either provide an explicit migration or fall back safely without widening routing or qualification permissions.

## Release process

The release workflow reads the package version from `pyproject.toml`. On an eligible `main` push it runs Ruff, the unit/repository audit suite, pinned ACL4SSR upstream parity, and changelog-version checks before creating a source-only GitHub Release if the tag does not already exist.

Release notes contain public source-code changes only. Generated production configuration, subscription data, scheduler state, AI cache, production metrics, previous-good bytes, Cloudflare KV values, and node-level qualification results are never release assets.
