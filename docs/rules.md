# Routing rules and ACL4SSR

`clash-relay` uses ACL4SSR as the canonical production routing source while keeping the final FlClash profile standalone.

## Production model

The canonical `config.yaml` enables `rule_sources.acl4ssr` and pins `rules/acl4ssr.yaml` to one immutable ACL4SSR commit. Production does not follow the moving `master` branch.

The production rule topology follows ACL4SSR's `ACL4SSR_Online_Full.ini` ordering, adapted to Mihomo and to clash-relay's single node-owning `Proxy` pool. The generated profile therefore uses ACL4SSR for routing globally instead of merging project-specific ChatGPT, Claude, Gemini, Google Play, or bulk routing rules.

During a trusted generation run:

1. subscription URLs are resolved from GitHub Secrets;
2. ACL4SSR rule fragments are fetched from the pinned public commit;
3. supported classical Clash rules are parsed and normalized;
4. each fetched ACL4SSR fragment becomes a `type: inline`, `behavior: classical` Mihomo `rule-provider` inside the private standalone profile;
5. the top-level `rules:` list contains compact `RULE-SET,<provider>,<policy>` mappings plus intentional top-level rules such as `GEOIP` and the final `MATCH`;
6. ACL4SSR policy groups reference `Proxy`, `Auto`, `Direct`, or built-ins without duplicating node credentials;
7. the exact standalone YAML is validated by both pinned Mihomo stable versions before Cloudflare KV publication.

FlClash does **not** need runtime access to GitHub or ACL4SSR. Generated rule providers contain no `url` or `path`; all ACL4SSR data required at runtime is already embedded in the one private YAML. No subscription URL, proxy credential, Cloudflare API token, or `PROFILE_TOKEN` is sent to ACL4SSR.

This differs deliberately from a conventional remote `rule-providers` profile. Runtime HTTP rule providers are convenient, but they make effective routing depend on an external host and potentially on a moving branch after the profile itself was validated. `clash-relay` instead keeps the pinned-build reproducibility boundary.

## Node, automatic routing, and policy groups

Only `Proxy` owns the normal production node provider. AI-labelled nodes are not excluded from the general pool, so ACL4SSR AI traffic can use the same global node inventory.

Internal automatic routing anchors are generated only when they add behavior:

- a pool with exactly one eligible automatic route is referenced directly through its `__CR_AUTO_<POOL>_<REGION>` group;
- a pool with multiple automatic routes receives one `__CR_FALLBACK_<POOL>` group in configured fallback order;
- an optional empty pool receives `__CR_FAIL_CLOSED_<POOL>` pointing to `REJECT`;
- a required empty pool aborts generation.

Canonical production uses `general.regions: [ANY]`, so both public `Proxy` and ACL4SSR `Auto` can share `__CR_AUTO_GENERAL_ANY` directly. There is no redundant `__CR_SERVICE_FALLBACK_GENERAL` layer.

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

## Generated rule-provider model

Each enabled ACL4SSR source receives a deterministic provider name derived from its manifest ID. For example:

```yaml
rule-providers:
  acl4ssr_ai:
    type: inline
    behavior: classical
    payload:
      - DOMAIN-SUFFIX,example.invalid

rules:
  - RULE-SET,acl4ssr_ai,AI
  - GEOIP,CN,Direct,no-resolve
  - MATCH,Final
```

The example payload above is illustrative; production payloads come from the exact pinned ACL4SSR commit.

The validator rejects remote rule providers, empty providers, non-`classical` ACL4SSR providers, provider URLs/paths, and `RULE-SET` references to unknown providers. This preserves a standalone, inspectable, fail-closed output.

Moving the rule data from thousands of top-level `rules:` rows into inline rule-provider payloads is a structural improvement, not an attempt to hide or discard data. The standalone YAML still contains the complete normalized ACL4SSR rule payload, while the routing table itself becomes small and auditable.

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

Unknown policy groups, unknown automatic pools, duplicate names, unsafe repository paths, missing `RULE-SET` providers, remote rule providers, or group cycles also fail closed. Every ACL4SSR pin or topology change must pass deterministic generation plus real Mihomo `v1.19.30` and `v1.19.29` validation before production publication.

## Licensing and attribution

ACL4SSR is distributed under **CC-BY-SA-4.0**. The repository does not vendor its bulk rule data into the MIT-licensed source tree. Generated profiles include attribution with the exact upstream commit and license reference.
