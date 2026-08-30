# Routing rules and ACL4SSR

`clash-relay` uses ACL4SSR as the canonical production routing source while keeping the final FlClash profile standalone.

## Production model

The canonical `config.yaml` enables only `general` plus `rule_sources.acl4ssr`. `rules/acl4ssr.yaml` pins one immutable ACL4SSR commit instead of following the moving `master` branch.

The production topology follows ACL4SSR's `ACL4SSR_Online_Full.ini` ordering, adapted to one node-owning pool named `节点选择`. Dedicated project routing for ChatGPT, Claude, Gemini, Google Play, bulk traffic, residential routes, EMBY, high-multiplier routes, and chain routes is not part of the production declaration anymore.

During a trusted generation run:

1. subscription URLs are resolved from GitHub Secrets;
2. ACL4SSR rule fragments are fetched from the pinned public commit;
3. supported classical Clash rules are parsed and normalized;
4. each fragment becomes a `type: inline`, `behavior: classical` Mihomo `rule-provider` inside the private standalone profile;
5. top-level `rules:` contains compact `RULE-SET,<provider>,<policy>` mappings plus intentional rules such as `GEOIP` and final `MATCH`;
6. Chinese policy groups reference `节点选择`, `直连`, or Mihomo built-ins without duplicating node credentials;
7. the exact standalone YAML is validated by Mihomo v1.19.30 and v1.19.29 before Cloudflare KV publication.

FlClash does **not** need runtime access to GitHub or ACL4SSR. Generated ACL4SSR rule providers contain no `url` or `path`; all required rule data is embedded in the one private YAML.

## Node and automatic routing

Production has one `general` pool:

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

The generic generator still retains true fallback semantics:

- one eligible automatic route -> reference that `__CR_AUTO_*` directly;
- multiple eligible automatic routes -> create `__CR_FALLBACK_*` in configured order;
- optional empty pool -> `__CR_FAIL_CLOSED_* -> REJECT`;
- required empty pool -> abort generation.

Therefore the former redundant `__CR_SERVICE_FALLBACK_GENERAL` layer remains absent without removing real multi-route fallback support.

## 17 visible Chinese policy groups

Canonical production exposes exactly:

```text
节点选择
直连
广告拦截
谷歌FCM
微软服务
苹果服务
电报消息
人工智能
网易音乐
游戏平台
油管视频
奈飞视频
巴哈姆特
哔哩哔哩
国内媒体
国外媒体
漏网之鱼
```

The simplification deliberately removes or merges four old selectors:

- `Auto` -> removed because `节点选择` already exposes the same automatic node anchor;
- `App Purify` -> merged into `广告拦截`;
- `Microsoft Bing` -> merged into `微软服务`;
- `Microsoft OneDrive` -> merged into `微软服务`.

This reduces visible groups from 21 to 17 while preserving the useful application/media distinctions.

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
  - GEOIP,CN,直连,no-resolve
  - MATCH,漏网之鱼
```

The example payload is illustrative; production payloads come from the exact pinned ACL4SSR commit.

The validator rejects remote rule providers, empty providers, non-`classical` ACL4SSR providers, provider URLs/paths, and `RULE-SET` references to unknown providers. Moving data into inline rule-provider payloads is structural organization, not data removal; the standalone YAML still contains the normalized ACL4SSR data required at runtime.

## ACL4SSR Full routing order

| Order | ACL4SSR source | Production target |
| ---: | --- | --- |
| 10 | `LocalAreaNetwork` | `直连` |
| 15 | `UnBan` | `直连` |
| 20 | `BanAD` | `广告拦截` |
| 30 | `BanProgramAD` | `广告拦截` |
| 40 | `GoogleFCM` | `谷歌FCM` |
| 50 | `GoogleCN` | `直连` |
| 60 | `SteamCN` | `直连` |
| 70 | `Bing` | `微软服务` |
| 80 | `OneDrive` | `微软服务` |
| 90 | `Microsoft` | `微软服务` |
| 100 | `Apple` | `苹果服务` |
| 110 | `Telegram` | `电报消息` |
| 120 | `AI` | `人工智能` |
| 125 | `OpenAi` | `人工智能` |
| 130 | `NetEaseMusic` | `网易音乐` |
| 140-160 | Epic / Origin / Sony / Steam / Nintendo | `游戏平台` |
| 170 | `YouTube` | `油管视频` |
| 180 | `Netflix` | `奈飞视频` |
| 190 | `Bahamut` | `巴哈姆特` |
| 200-210 | `BilibiliHMT` / `Bilibili` | `哔哩哔哩` |
| 220 | `ChinaMedia` | `国内媒体` |
| 230 | `ProxyMedia` | `国外媒体` |
| 800 | `ProxyGFWlist` | `节点选择` |
| 900 | `ChinaDomain` | `直连` |
| 910 | `ChinaCompanyIp` | `直连` |
| 915 | `Download` | `直连` |
| 920 | `GEOIP,CN` | `直连` |
| final | `MATCH` | `漏网之鱼` |

The project intentionally does not duplicate ACL4SSR's region-regex node groups. Subscription nodes are normalized once into `节点选择`; application/media policy groups choose between `节点选择` and `直连` according to their default order.

## Policy defaults

Direct-preferring selectors use:

```text
直连
节点选择
```

Proxy-preferring selectors use:

```text
节点选择
直连
```

`广告拦截` uses `REJECT` first with `DIRECT` as the manual override. `漏网之鱼` defaults to `节点选择` and can be manually changed to `直连`.

## Compatibility handling

The adapter accepts rule types supported by the project's Mihomo output model, including domain, IP-CIDR, process, port, and network rules. Legacy ACL4SSR `URL-REGEX` and `USER-AGENT` entries are skipped and counted. Any other unknown rule type fails generation closed.

Unknown policy groups, duplicate names, unsafe repository paths, missing `RULE-SET` providers, remote rule providers, or group cycles also fail closed. Every ACL4SSR pin or topology change must pass deterministic generation plus real Mihomo v1.19.30 and v1.19.29 validation before production publication.

## Generic engine isolation

The reusable engine still supports richer service/pool/capability declarations. Those regression cases now live under `tests/fixtures/project/` and no longer reuse root production YAML. This keeps generic coverage without forcing disabled groups into the real FlClash profile.

## Licensing and attribution

ACL4SSR is distributed under **CC-BY-SA-4.0**. The repository does not vendor its bulk rule data into the MIT-licensed source tree. Generated profiles include attribution with the exact upstream commit and license reference.
