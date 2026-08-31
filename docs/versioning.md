# Versioning and compatibility

`clash-relay` follows Semantic Versioning beginning with `v0.1.0`.

## Compatibility contract

A patch release may fix bugs, harden validation, improve diagnostics, or update implementation details without changing documented source-use and routing semantics.

A minor release may add opt-in configuration fields, probes, policy capabilities, or additional safe observability while preserving existing declarations by default.

A major release is required when a documented default routing invariant, configuration field meaning, persistent-state format, or supported migration path changes incompatibly.

## Canonical v0.1.0 invariants

- A source declaring `allowed_uses: [browsing, ai]` cannot become reachable from a `general` route, even through nested groups, fallback groups, provider indirection, or `dialer-proxy`.
- The canonical first subscription rejects only explicit multipliers strictly greater than `2.0`; exactly `2.0` and unmarked nodes remain eligible.
- `ProxyGFWlist` is the portable generic browsing route and targets `网页浏览`.
- Final `MATCH` deliberately remains `漏网之鱼` on the general graph.
- Browsing qualification is live and fail-closed: 3/3 is stable automatic, 2/3 is reserve/manual, and fewer than 2/3 is rejected from browsing.
- Historical scheduler state may narrow automatic preference but must never promote a node that fails the current live qualification boundary.
- OpenAI, Claude, and Gemini are qualified independently and fail closed per service.
- The exact production candidate is validated with Mihomo v1.19.30 and v1.19.29 before publication.
- Production bytes are private and are not attached to GitHub Releases.

## Release process

The repository release workflow reads the package version from `pyproject.toml`. On an eligible `main` push it verifies that `CHANGELOG.md` contains the matching release section, reruns the complete unit and repository audit suite, and creates an annotated Git tag and GitHub Release only if the tag does not already exist.

Release notes contain public source-code changes only. Generated production configuration, subscription data, scheduler state, Cloudflare KV values, and node-level qualification results are never release assets.
