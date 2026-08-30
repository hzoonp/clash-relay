# Routing rules and ACL4SSR

`clash-relay` adapts selected Clash rule fragments from [ACL4SSR/ACL4SSR](https://github.com/ACL4SSR/ACL4SSR) while keeping the final FlClash profile standalone.

## Design

The canonical production configuration enables `rule_sources.acl4ssr` and points to `rules/acl4ssr.yaml`. The manifest pins one immutable upstream Git commit instead of following `master` at generation time.

During a trusted generation run:

1. subscription URLs are resolved from GitHub Secrets as before;
2. selected ACL4SSR rule fragments are fetched over HTTPS from the pinned public commit;
3. supported classical Clash rules are parsed and normalized;
4. lightweight policy groups are created by referencing existing clash-relay proxy groups and built-ins rather than copying proxy payloads;
5. rules are merged with the project-specific ChatGPT, Claude, Gemini, Google Play and bulk rules in deterministic priority order;
6. the resulting rules are written inline into the private generated Mihomo YAML;
7. the exact standalone YAML is validated by both pinned Mihomo stable versions before Cloudflare KV publication.

FlClash therefore does **not** need runtime access to GitHub or ACL4SSR. No subscription URL, proxy credential, Cloudflare token, or `PROFILE_TOKEN` is sent to ACL4SSR; the only ACL4SSR traffic is public HTTPS GET requests for rule fragments.

## Policy-group model

The node-owning groups remain responsible for node eligibility, probes and manual node selection:

- `Proxy` — normal browsing nodes;
- `ChatGPT`, `Claude`, `Gemini` — explicitly AI-capable nodes;
- `Google Play` — nodes checked with the Google Play probe;
- `Video & Downloads` — bulk/media-capable nodes;
- optional `Residential`, `EMBY`, `High Multiplier`, and `Chain` groups when their modules are enabled.

ACL4SSR-specific groups are policy-only selectors. They contain no copied node credentials:

| Group | Default choice | Alternatives |
| --- | --- | --- |
| `Auto` | automatic fallback of `Proxy` | none |
| `Direct` | `DIRECT` | `Proxy`, `Auto` |
| `Block` | `REJECT` | `DIRECT` |
| `App Purify` | `REJECT` | `DIRECT` |
| `Google FCM` | `Proxy` | `Direct`, `Auto` |
| `Microsoft` | `Direct` | `Proxy` |
| `Apple` | `Proxy` | `Direct` |
| `Telegram` | `Proxy` | `Direct` |
| `Final` | `Proxy` | `Direct`, `Auto` |

This separation keeps FlClash controls understandable without multiplying inline provider payloads for every rule category.

## Canonical routing order

The production manifest currently maps the selected rule sets as follows. Lower priority numbers match first.

| Priority | Source | Target |
| ---: | --- | --- |
| 10 | ACL4SSR `LocalAreaNetwork` | `Direct` |
| 15 | ACL4SSR `UnBan` | `Direct` |
| 20 | ACL4SSR `BanAD` | `Block` |
| 30 | ACL4SSR `BanProgramAD` | `App Purify` |
| 90 | ACL4SSR `GoogleFCM` | `Google FCM` |
| 100 | project ChatGPT rules | `ChatGPT` |
| 110 | project Claude rules | `Claude` |
| 120 | project Gemini rules | `Gemini` |
| 130 | ACL4SSR `GoogleCN` | `Direct` |
| 140 | ACL4SSR `SteamCN` | `Direct` |
| 200 | project Google Play rules | `Google Play` |
| 210 | ACL4SSR `Microsoft` | `Microsoft` |
| 220 | ACL4SSR `Apple` | `Apple` |
| 250 | project EMBY rules, when enabled | `EMBY` |
| 260 | ACL4SSR `Telegram` | `Telegram` |
| 270 | ACL4SSR `ProxyMedia` | `Video & Downloads` |
| 300 | project bulk/download rules | `Video & Downloads` |
| 800 | ACL4SSR `ProxyLite` | `Proxy` |
| 900 | ACL4SSR `ChinaDomain` | `Direct` |
| 910 | ACL4SSR `ChinaCompanyIp` | `Direct` |
| 920 | `GEOIP,CN` | `Direct` |
| final | `MATCH` | `Final` |

The dedicated AI rules deliberately precede ACL4SSR `ProxyMedia` and `ProxyLite`, because those broader lists also contain AI-related domains. This preserves the distinct ChatGPT, Claude and Gemini policy groups.

## Compatibility handling

The adapter accepts rule types that are supported by this project's Mihomo output model, including domain, IP-CIDR, process, port and network rules. Legacy ACL4SSR `URL-REGEX` and `USER-AGENT` entries are intentionally skipped and counted in the generation report rather than silently converted. Any other unknown rule type fails generation closed.

Routing-group references are validated before fetching subscriptions. Unknown groups, unknown automatic pools, duplicate names, and routing-group cycles fail generation closed. Every change to the pinned ACL4SSR commit or group topology must pass the same deterministic tests and real Mihomo `v1.19.30` / `v1.19.29` validation before production publication.

## Licensing and attribution

ACL4SSR states that its rule project is distributed under **CC-BY-SA-4.0**. The repository does not vendor ACL4SSR's bulk rule data into the MIT-licensed source tree. When ACL4SSR routing is enabled, the generated profile includes an attribution comment containing the exact upstream commit and the CC-BY-SA-4.0 license reference.

The upstream pin in `rules/acl4ssr.yaml` is the reproducibility and audit boundary. Updating it is an explicit code review change; production never silently follows a moving branch.
