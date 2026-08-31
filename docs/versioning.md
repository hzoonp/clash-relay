# Versioning and compatibility

`clash-relay` follows Semantic Versioning. `v0.1.0` established the first production baseline; `v1.0.0` froze the public compatibility contract; `v1.0.1` hardened the browsing runtime; and `v1.1.0` adds browsing-region scheduling as a backward-compatible Routing V2 capability without widening source permissions.

## Compatibility contract

A patch release may fix bugs, harden validation, improve diagnostics, tune privacy-safe implementation details, or update supported dependency/core pins without changing documented source-use and routing semantics.

A minor release may add opt-in or backward-compatible configuration fields, probes, policy capabilities, regional scheduling dimensions, safe observability, or compatible state behavior while preserving existing security boundaries.

A major release is required when a documented default routing invariant, configuration field meaning, persistent-state migration path, security boundary, or public deployment contract changes incompatibly.

## Canonical v1 invariants

- A source declaring `allowed_uses: [browsing, ai]` cannot become reachable from a `general` route, even through nested groups, fallback groups, provider indirection, or `dialer-proxy`.
- The canonical first subscription rejects only explicit multipliers strictly greater than `2.0`; exactly `2.0` and unmarked nodes remain eligible.
- `ProxyGFWlist` is the portable generic browsing route and targets `网页浏览`.
- Final `MATCH` deliberately remains `漏网之鱼` on the general graph.
- The canonical top-level user-facing groups remain `代理选择`, `网页浏览`, and `人工智能`. These public scenario groups do not attach proxy providers directly.
- Browsing owns an independent regional order declared as `preferred_regions: [US, SG, JP, TW, KR, HK, OTHER]`; changing browsing preference does not change the AI service preference order.
- `网页浏览` contains `网页自动`, every currently available `网页 · <地区>` fixed-region choice in preference order, and `DIRECT`. Raw `[BROWSING:*]` runtime nodes are never direct members of the public selector.
- Browsing qualification is live and fail-closed. Canonical policy is three attempts: 3/3 is Stable, 2/3 is Reserve, and fewer than 2/3 is rejected from browsing for that publication.
- Automatic browsing routing is region-first: preferred-region Stable, then same-region Reserve, then the next available region. A lower instantaneous delay in another healthy region is not sufficient reason to switch regions.
- A manual regional browsing choice never crosses regions. It may recover from Stable to same-region Reserve, but another country's nodes are not valid members of that regional group.
- Completely unavailable browsing regions may be omitted after live qualification, but publication fails if no browsing region retains a qualified node.
- Browsing pre-publication qualification, provider health checks, regional Stable/Reserve schedulers, regional fallbacks, and the browsing automatic fallback use the canonical HTTPS probe semantics. `scheduler.browsing.region_switch_interval` controls cross-region re-evaluation and must not be shorter than the browsing probe interval.
- Historical scheduler state may narrow the preferred current Stable subset. A historically demoted but currently qualified node moves to Reserve inside the same region rather than disappearing from automatic failover eligibility. History must never promote a current Reserve or live-failed node into Stable.
- OpenAI, Claude, and Gemini are qualified independently and fail closed per service. AI qualification cache is private, payload-sensitive, TTL-bounded, and falls back to live probing on changed, expired, missing, corrupt, or unavailable entries.
- `policies.yaml` may declare supported scheduler controls. Omitting the optional `scheduler:` block keeps conservative defaults; declaring it cannot alter source permissions or bypass reachability audits.
- The exact production candidate is validated with Mihomo v1.19.30 and v1.19.29 before publication.
- A failed mandatory gate does not intentionally replace the last validated production value. Auxiliary history/cache/metrics state cannot relax a live safety gate.
- Manual rollback requires explicit confirmation and revalidates previous-good bytes with both supported Mihomo cores before activation.
- Production configuration, previous-good bytes, scheduler state, AI cache, production metrics, subscription responses, and node-level qualification results are private operational data and are not attached to GitHub Releases.
- Production subscription fetching defaults to HTTPS and rejects URL userinfo, private/special-use IP literals, localhost names, and DNS resolutions containing private/special-use IPv4 or IPv6 addresses.

## Persistent state compatibility

Persistent state is auxiliary and must degrade safely when missing or invalid.

- Browsing scheduler state remains privacy-preserving and keyed by private HMAC fingerprints. Regional scheduling applies historical demotion separately inside each region; persistent node records do not contain node names, endpoints, credentials, or subscription URLs.
- AI qualification cache v1 is keyed by a private HMAC of provider identity plus the full proxy payload, so payload changes automatically invalidate reuse.
- Production metrics v1 is aggregate-only and retained as a bounded 30-run ring.
- A future incompatible state format must either provide an explicit migration or fall back safely without widening routing or qualification permissions.

## Release process

The repository release workflow reads the package version from `pyproject.toml`. On an eligible `main` push it verifies that `CHANGELOG.md` contains the matching release section, reruns the complete unit and repository audit suite, and creates an annotated Git tag and GitHub Release only if the tag does not already exist.

Release notes contain public source-code changes only. Generated production configuration, subscription data, scheduler state, AI cache, production metrics, previous-good bytes, Cloudflare KV values, and node-level qualification results are never release assets.
