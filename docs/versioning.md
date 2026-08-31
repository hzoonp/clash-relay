# Versioning and compatibility

`clash-relay` follows Semantic Versioning. `v0.1.0` established the first production baseline; `v1.0.0` freezes the public compatibility contract described here.

## Compatibility contract

A patch release may fix bugs, harden validation, improve diagnostics, tune privacy-safe implementation details, or update supported dependency/core pins without changing documented source-use and routing semantics.

A minor release may add opt-in configuration fields, probes, policy capabilities, safe observability, or backward-compatible state versions while preserving existing declarations by default.

A major release is required when a documented default routing invariant, configuration field meaning, persistent-state migration path, security boundary, or public deployment contract changes incompatibly.

## Canonical v1 invariants

- A source declaring `allowed_uses: [browsing, ai]` cannot become reachable from a `general` route, even through nested groups, fallback groups, provider indirection, or `dialer-proxy`.
- The canonical first subscription rejects only explicit multipliers strictly greater than `2.0`; exactly `2.0` and unmarked nodes remain eligible.
- `ProxyGFWlist` is the portable generic browsing route and targets `网页浏览`.
- Final `MATCH` deliberately remains `漏网之鱼` on the general graph.
- Browsing qualification is live and fail-closed. Canonical policy is three attempts: 3/3 is stable automatic, 2/3 is reserve/manual, and fewer than 2/3 is rejected from browsing.
- Historical scheduler state may narrow the current live-stable automatic subset but must never promote a reserve or live-failed node. Old v1 scheduler state migrates to v2 without changing the private HMAC fingerprint domain.
- OpenAI, Claude, and Gemini are qualified independently and fail closed per service. AI qualification cache is private, payload-sensitive, TTL-bounded, and falls back to live probing on changed, expired, missing, corrupt, or unavailable entries.
- `policies.yaml` may declare supported scheduler controls. Omitting the optional `scheduler:` block keeps the v0.1.0-compatible defaults; declaring it cannot alter source permissions or bypass reachability audits.
- The exact production candidate is validated with Mihomo v1.19.30 and v1.19.29 before publication.
- A failed mandatory gate does not intentionally replace the last validated production value. Auxiliary history/cache/metrics state cannot relax a live safety gate.
- Manual rollback requires explicit confirmation and revalidates previous-good bytes with both supported Mihomo cores before activation.
- Production configuration, previous-good bytes, scheduler state, AI cache, production metrics, subscription responses, and node-level qualification results are private operational data and are not attached to GitHub Releases.
- Production subscription fetching defaults to HTTPS and rejects URL userinfo, private/special-use IP literals, localhost names, and DNS resolutions containing private/special-use IPv4 or IPv6 addresses.

## Persistent state compatibility

Persistent state is auxiliary and must degrade safely when missing or invalid.

- Browsing scheduler state v1 is readable and migrates to v2; v2 is the current write format.
- AI qualification cache v1 is keyed by a private HMAC of provider identity plus the full proxy payload, so payload changes automatically invalidate reuse.
- Production metrics v1 is aggregate-only and retained as a bounded 30-run ring.
- A future incompatible state format must either provide an explicit migration or fall back safely without widening routing or qualification permissions.

## Release process

The repository release workflow reads the package version from `pyproject.toml`. On an eligible `main` push it verifies that `CHANGELOG.md` contains the matching release section, reruns the complete unit and repository audit suite, and creates an annotated Git tag and GitHub Release only if the tag does not already exist.

Release notes contain public source-code changes only. Generated production configuration, subscription data, scheduler state, AI cache, production metrics, previous-good bytes, Cloudflare KV values, and node-level qualification results are never release assets.
