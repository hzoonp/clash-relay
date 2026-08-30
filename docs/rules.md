# Routing rules and ACL4SSR

`clash-relay` uses ACL4SSR as the canonical production routing source while keeping the final FlClash profile standalone.

## Production model

The canonical `config.yaml` enables only `general` plus `rule_sources.acl4ssr`. `rules/acl4ssr.yaml` pins one immutable ACL4SSR commit instead of following the moving `master` branch.

During a trusted generation run:

1. subscription URLs are resolved from GitHub Secrets;
2. subscription-scoped node admission policies are applied before node classification;
3. ACL4SSR rule fragments are fetched from the pinned public commit;
4. supported classical Clash rules are parsed and normalized;
5. each fragment becomes a `type: inline`, `behavior: classical` Mihomo `rule-provider` inside the private standalone profile;
6. top-level `rules:` contains compact `RULE-SET,<provider>,<policy>` mappings plus intentional rules such as `GEOIP` and final `MATCH`;
7. source-scoped routing restrictions are applied by hidden filtered anchors that reuse the existing inline proxy provider;
8. the exact standalone YAML is validated by Mihomo v1.19.30 and v1.19.29 before Cloudflare KV publication.

FlClash does **not** need runtime access to GitHub or ACL4SSR. Generated ACL4SSR rule providers contain no `url` or `path`; all required rule data is embedded in the one private YAML.

## Node and automatic routing

Production has one node-owning `general` pool:

```text
节点选择
    ↓
__CR_AUTO_GENERAL_ANY
    ↓
inline proxy-provider
    ↓
真实节点
```

A separate public `Auto` group is deliberately not generated because it would point to the same single automatic anchor and add no routing behavior.

The generic generator retains real fallback semantics:

- one eligible automatic route -> reference that `__CR_AUTO_*` directly;
- multiple eligible automatic routes -> create `__CR_FALLBACK_*` in configured order;
- optional empty pool -> `__CR_FAIL_CLOSED_* -> REJECT`;
- required empty pool -> abort generation.

Therefore the former redundant `__CR_SERVICE_FALLBACK_GENERAL` layer remains absent without removing real multi-route fallback support.

## Five visible policy groups

Canonical production exposes exactly:

```text
节点选择
人工智能
流媒体
国内服务
广告拦截
```

Rules that do not need a user-facing override target Mihomo built-ins directly. This keeps ACL4SSR Full coverage without turning every application family into another FlClash selector.

The policy defaults are:

```text
人工智能: 节点选择 -> DIRECT
流媒体:   过滤后的代理路径 -> DIRECT
国内服务: DIRECT -> 过滤后的代理路径
广告拦截: REJECT -> DIRECT
```

`节点选择` is the only public group that owns manual provider choices. The other four are lightweight routing policies and do not duplicate proxy credentials.

## Generated rule-provider model

Each enabled ACL4SSR source receives a deterministic provider name derived from its manifest ID. Example:

```yaml
rule-providers:
  acl4ssr_ai:
    type: inline
    behavior: classical
    payload:
      - DOMAIN-SUFFIX,example.invalid

rules:
  - RULE-SET,acl4ssr_ai,人工智能
  - GEOIP,CN,DIRECT,no-resolve
  - MATCH,节点选择
```

The example payload is illustrative; production payloads come from the exact pinned ACL4SSR commit.

The validator rejects remote rule providers, empty providers, non-`classical` ACL4SSR providers, provider URLs/paths, and `RULE-SET` references to unknown providers. Moving data into inline rule-provider payloads is structural organization, not data removal; the standalone YAML still contains the normalized ACL4SSR data required at runtime.

## ACL4SSR Full routing order

| Order | ACL4SSR source | Production target |
| ---: | --- | --- |
| 10 | `LocalAreaNetwork` | `DIRECT` |
| 15 | `UnBan` | `DIRECT` |
| 20 | `BanAD` | `广告拦截` |
| 30 | `BanProgramAD` | `广告拦截` |
| 40 | `GoogleFCM` | `国内服务` |
| 50 | `GoogleCN` | `DIRECT` |
| 60 | `SteamCN` | `DIRECT` |
| 70 | `Bing` | `国内服务` |
| 80 | `OneDrive` | `国内服务` |
| 90 | `Microsoft` | `国内服务` |
| 100 | `Apple` | `国内服务` |
| 110 | `Telegram` | source-filtered proxy path |
| 120 | `AI` | `人工智能` |
| 125 | `OpenAi` | `人工智能` |
| 130 | `NetEaseMusic` | `国内服务` |
| 140-160 | Epic / Origin / Sony / Steam / Nintendo | `国内服务` |
| 170 | `YouTube` | `流媒体` |
| 180 | `Netflix` | `流媒体` |
| 190 | `Bahamut` | `流媒体` |
| 200-210 | `BilibiliHMT` / `Bilibili` | `国内服务` |
| 220 | `ChinaMedia` | `国内服务` |
| 230 | `ProxyMedia` | `流媒体` |
| 800 | `ProxyGFWlist` | `节点选择` |
| 900 | `ChinaDomain` | `DIRECT` |
| 910 | `ChinaCompanyIp` | `DIRECT` |
| 915 | `Download` | `DIRECT` |
| 920 | `GEOIP,CN` | `DIRECT` |
| final | `MATCH` | source-filtered proxy path |

## Subscription-scoped admission and routing

A subscription may declare `max_node_multiplier`. The admission filter examines only explicit multiplier markers in the node name, including common forms such as `2x`, `x2.5`, `3倍`, and `倍率:4`.

For a ceiling of `2.0`:

- explicit multiplier `<= 2.0` -> retained;
- explicit multiplier `> 2.0` -> removed before classification and provider generation;
- no explicit multiplier marker -> retained rather than guessed.

Production additionally restricts `subscription_1` to the intended generic web and AI routes:

- `ProxyGFWlist` may use `subscription_1`;
- `AI` and `OpenAi` may use `subscription_1` through `人工智能`;
- `流媒体` excludes `subscription_1` from its proxy path;
- `国内服务` excludes `subscription_1` from its proxy path;
- `Telegram` excludes `subscription_1`;
- final unmatched `MATCH` traffic excludes `subscription_1`.

This is a rule-routing restriction, not process identification. In rule mode it confines `subscription_1` to the explicit ACL4SSR generic-web and AI paths above; it does not attempt to prove that the originating application executable is a browser.

Source exclusions do not copy proxy credentials into another provider. Instead, the generator clones only the hidden routing anchor and applies Mihomo `exclude-filter` to the shared provider:

```text
流媒体 / 国内服务 / Telegram / final
        ↓
__CR_AUTO_FILTER_<digest>
        ↓ exclude-filter: subscription_1 runtime prefix
cr_general_any
        ↓
同一份真实节点
```

If every candidate behind a restricted route belongs to an excluded source, the generated route fails closed to a hidden `REJECT` group rather than silently falling back to the prohibited subscription.

## Compatibility handling

The adapter accepts rule types supported by the project's Mihomo output model, including domain, IP-CIDR, process, port, and network rules. Legacy ACL4SSR `URL-REGEX` and `USER-AGENT` entries are skipped and counted. Any other unknown rule type fails generation closed.

Unknown policy groups, duplicate names, unsafe repository paths, missing `RULE-SET` providers, remote rule providers, unknown source-exclusion IDs, or group cycles also fail closed. Every ACL4SSR pin or topology change must pass deterministic generation plus real Mihomo v1.19.30 and v1.19.29 validation before production publication.

## Generic engine isolation

The reusable engine still supports richer service/pool/capability declarations. Those regression cases live under `tests/fixtures/project/` and do not force disabled groups into the real FlClash profile.

## Licensing and attribution

ACL4SSR is distributed under **CC-BY-SA-4.0**. The repository does not vendor its bulk rule data into the MIT-licensed source tree. Generated profiles include attribution with the exact upstream commit and license reference.
