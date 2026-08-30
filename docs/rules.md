# Routing rules and ACL4SSR

`clash-relay` can adapt selected Clash rule fragments from [ACL4SSR/ACL4SSR](https://github.com/ACL4SSR/ACL4SSR) while keeping the final FlClash profile standalone.

## Design

The canonical production configuration enables `rule_sources.acl4ssr` and points to `rules/acl4ssr.yaml`. The manifest pins one immutable upstream Git commit instead of following `master` at generation time.

During a trusted generation run:

1. subscription URLs are resolved from GitHub Secrets as before;
2. selected ACL4SSR rule fragments are fetched over HTTPS from the pinned public commit;
3. supported classical Clash rules are parsed and normalized;
4. rules are merged with the project-specific ChatGPT, Claude, Gemini, Google Play and bulk rules in deterministic priority order;
5. the resulting rules are written inline into the private generated Mihomo YAML;
6. the exact standalone YAML is validated by both pinned Mihomo stable versions before Cloudflare KV publication.

FlClash therefore does **not** need runtime access to GitHub or ACL4SSR. No subscription URL, proxy credential, Cloudflare token, or `PROFILE_TOKEN` is sent to ACL4SSR; the only ACL4SSR traffic is public HTTPS GET requests for rule fragments.

## Canonical routing order

The production manifest currently maps the selected rule sets as follows. Lower priority numbers match first.

| Priority | Source | Target |
| ---: | --- | --- |
| 10 | ACL4SSR `LocalAreaNetwork` | `DIRECT` |
| 20 | ACL4SSR `BanAD` | `REJECT` |
| 30 | ACL4SSR `BanProgramAD` | `REJECT` |
| 100 | project ChatGPT rules | `ChatGPT` |
| 110 | project Claude rules | `Claude` |
| 120 | project Gemini rules | `Gemini` |
| 130 | ACL4SSR `GoogleCN` | `DIRECT` |
| 140 | ACL4SSR `SteamCN` | `DIRECT` |
| 200 | project Google Play rules | `Google Play` |
| 210 | ACL4SSR `Microsoft` | `DIRECT` |
| 220 | ACL4SSR `Apple` | `Proxy` |
| 250 | project EMBY rules, when enabled | `EMBY` |
| 270 | ACL4SSR `ProxyMedia` | `Video & Downloads` |
| 300 | project bulk/download rules | `Video & Downloads` |
| 400 | ACL4SSR `Telegram` | `Proxy` |
| 800 | ACL4SSR `ProxyLite` | `Proxy` |
| 900 | ACL4SSR `ChinaDomain` | `DIRECT` |
| 910 | ACL4SSR `ChinaCompanyIp` | `DIRECT` |
| 920 | `GEOIP,CN` | `DIRECT` |
| final | `MATCH` | `Proxy` |

The dedicated AI rules deliberately precede ACL4SSR `ProxyMedia` and `ProxyLite`, because those broader lists also contain AI-related domains. This preserves the distinct ChatGPT, Claude and Gemini policy groups.

## Compatibility handling

The adapter accepts rule types that are supported by this project's Mihomo output model, including domain, IP-CIDR, process, port and network rules. Legacy ACL4SSR `URL-REGEX` and `USER-AGENT` entries are intentionally skipped and counted in the generation report rather than silently converted. Any other unknown rule type fails generation closed.

Every change to the pinned ACL4SSR commit must pass the same deterministic tests and real Mihomo `v1.19.30` / `v1.19.29` validation before production publication.

## Licensing and attribution

ACL4SSR states that its rule project is distributed under **CC-BY-SA-4.0**. The repository does not vendor ACL4SSR's bulk rule data into the MIT-licensed source tree. When ACL4SSR routing is enabled, the generated profile includes an attribution comment containing the exact upstream commit and the CC-BY-SA-4.0 license reference.

The upstream pin in `rules/acl4ssr.yaml` is the reproducibility and audit boundary. Updating it is an explicit code review change; production never silently follows a moving branch.
