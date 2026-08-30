# Routing rules and ACL4SSR

`clash-relay` uses ACL4SSR as the canonical production routing source while keeping the final FlClash profile standalone.

## Production model

The canonical `config.yaml` enables `rule_sources.acl4ssr` and pins `rules/acl4ssr.yaml` to one immutable ACL4SSR commit. Production does not follow the moving `master` branch.

The production rule topology follows ACL4SSR's `ACL4SSR_Online_Full.ini` ordering, adapted to Mihomo and to clash-relay's single node-owning `Proxy` pool. The generated profile therefore uses ACL4SSR for routing globally instead of merging project-specific ChatGPT, Claude, Gemini, Google Play, or bulk routing rules.

During a trusted generation run:

1. subscription URLs are resolved from GitHub Secrets;
2. ACL4SSR rule fragments are fetched from the pinned public commit;
3. supported classical Clash rules are parsed and normalized;
4. ACL4SSR policy groups reference `Proxy`, `Auto`, `Direct`, or built-ins without duplicating node credentials;
5. all adapted ACL4SSR rules are written inline into the private generated Mihomo YAML;
6. the exact standalone YAML is validated by both pinned Mihomo stable versions before Cloudflare KV publication.

FlClash does **not** need runtime access to GitHub or ACL4SSR. No subscription URL, proxy credential, Cloudflare API token, or `PROFILE_TOKEN` is sent to ACL4SSR.

## Node and policy groups

Only `Proxy` owns the normal production node provider. `Auto` references its automatic fallback. AI-labelled nodes are not excluded from the general pool, so ACL4SSR AI traffic can use the same global node inventory.

ACL4SSR policy-only selectors include:

- `Direct`
- `Block`
- `App Purify`
- `Google FCM`
- `Microsoft Bing`
- `Microsoft OneDrive`
- `Microsoft`
- `Apple`
- `Telegram`
- `AI`
- `NetEase Music`
- `Games`
- `YouTube`
- `Netflix`
- `Bahamut`
- `Bilibili`
- `Domestic Media`
- `Foreign Media`
- `Final`

The former production `ChatGPT`, `Claude`, and `Gemini` groups are disabled. Their traffic is covered by ACL4SSR `AI.list` and `OpenAi.list` and is sent to the single `AI` policy group.

## ACL4SSR Full routing order

The manifest mirrors the meaningful rule order from `Clash/config/ACL4SSR_Online_Full.ini`:

| Order | ACL4SSR source | Target |
| ---: | --- | --- |
| 10 | `LocalAreaNetwork` | `Direct` |
| 15 | `UnBan` | `Direct` |
| 20 | `BanAD` | `Block` |
| 30 | `BanProgramAD` | `App Purify` |
| 40 | `GoogleFCM` | `Google FCM` |
| 50 | `GoogleCN` | `Direct` |
| 60 | `SteamCN` | `Direct` |
| 70 | `Bing` | `Microsoft Bing` |
| 80 | `OneDrive` | `Microsoft OneDrive` |
| 90 | `Microsoft` | `Microsoft` |
| 100 | `Apple` | `Apple` |
| 110 | `Telegram` | `Telegram` |
| 120 | `AI` | `AI` |
| 125 | `OpenAi` | `AI` |
| 130 | `NetEaseMusic` | `NetEase Music` |
| 140-160 | Epic / Origin / Sony / Steam / Nintendo | `Games` |
| 170 | `YouTube` | `YouTube` |
| 180 | `Netflix` | `Netflix` |
| 190 | `Bahamut` | `Bahamut` |
| 200-210 | `BilibiliHMT` / `Bilibili` | `Bilibili` |
| 220 | `ChinaMedia` | `Domestic Media` |
| 230 | `ProxyMedia` | `Foreign Media` |
| 800 | `ProxyGFWlist` | `Proxy` |
| 900 | `ChinaDomain` | `Direct` |
| 910 | `ChinaCompanyIp` | `Direct` |
| 915 | `Download` | `Direct` |
| 920 | `GEOIP,CN` | `Direct` |
| final | `MATCH` | `Final` |

ACL4SSR Full also defines region-specific selector groups. This project intentionally does not duplicate those regex-based node groups: subscription nodes are normalized once into `Proxy`, and policy groups can choose `Proxy`, `Auto`, or `Direct`. This keeps credentials single-owned and avoids multiplying inline providers.

## Compatibility handling

The adapter accepts rule types supported by the project's Mihomo output model, including domain, IP-CIDR, process, port, and network rules. Legacy ACL4SSR `URL-REGEX` and `USER-AGENT` entries are skipped and counted. Any other unknown rule type fails generation closed.

Unknown policy groups, unknown automatic pools, duplicate names, unsafe repository paths, or group cycles also fail closed. Every ACL4SSR pin or topology change must pass deterministic generation plus real Mihomo `v1.19.30` and `v1.19.29` validation before production publication.

## Licensing and attribution

ACL4SSR is distributed under **CC-BY-SA-4.0**. The repository does not vendor its bulk rule data into the MIT-licensed source tree. Generated profiles include attribution with the exact upstream commit and license reference.
