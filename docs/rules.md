# Routing rules and ACL4SSR

Production follows one rule: **ACL4SSR owns non-AI routing semantics; clash-relay owns presentation and live AI qualification.**

## Canonical source

`rules/acl4ssr.yaml` pins:

```text
repository: ACL4SSR/ACL4SSR
ref: c498ae4911f15b19c5ceaef6f8737ca8705b4430
license: CC-BY-SA-4.0
```

The build fetches the selected `ACL4SSR_Online_Full.ini` rule fragments from that immutable commit, parses supported classical Clash rules, and embeds every fragment as a `type: inline`, `behavior: classical` Mihomo rule provider. The final private profile therefore has no runtime dependency on GitHub or ACL4SSR.

Canonical production has no local rule prelude: `rules/direct.yaml` intentionally contains an empty rule list.

## What “strict” means

For every non-AI rule path, the canonical manifest preserves:

- upstream Full source order;
- upstream rule target;
- upstream semantic policy group;
- upstream policy member order;
- upstream country/manual/automatic selector regex and ordering;
- upstream `GEOIP,CN` target;
- upstream final `MATCH` target.

The project must not collapse Bilibili into a generic domestic group, Telegram into generic node selection, media applications into one routing target, or `MATCH` into a project-defined source-filtered path.

The pinned Full lists contain nine legacy `URL-REGEX` rules that Mihomo 1.19.x cannot express as classical rules: seven in `Download.list`, one in `ChinaMedia.list`, and one in `ProxyMedia.list`. clash-relay does **not** invent approximate `DOMAIN-REGEX` replacements. Instead, an omission is allowed only when the exact same rule is explicitly commented out by ACL4SSR's maintained `Clash/Providers/*.yaml` representation at the same immutable commit.

Canonical CI therefore requires:

```text
verified_compatibility_omissions == 9
unverified_legacy_rules == 0
```

The nine verified omissions are:

```text
Download.list   -> Clash/Providers/Download.yaml   -> 7
ChinaMedia.list -> Clash/Providers/ChinaMedia.yaml -> 1
ProxyMedia.list -> Clash/Providers/ProxyMedia.yaml -> 1
```

If a future pin introduces another Mihomo-incompatible rule, changes one of those exact rules, removes the matching upstream Provider comment, or moves compatibility evidence outside `Clash/Providers/`, generation fails closed. This makes the compatibility boundary explicit and upstream-grounded rather than silently dropping rules.

## Canonical routing map

The pinned Full semantics used by production are:

| Order | ACL4SSR source | Semantic target |
| ---: | --- | --- |
| 10 | `LocalAreaNetwork` | `全球直连` |
| 15 | `UnBan` | `全球直连` |
| 20 | `BanAD` | `广告拦截` |
| 30 | `BanProgramAD` | `应用净化` |
| 40 | `GoogleFCM` | `谷歌FCM` |
| 50 | `GoogleCN` | `全球直连` |
| 60 | `SteamCN` | `全球直连` |
| 70 | `Bing` | `微软Bing` |
| 80 | `OneDrive` | `微软云盘` |
| 90 | `Microsoft` | `微软服务` |
| 100 | `Apple` | `苹果服务` |
| 110 | `Telegram` | `电报消息` |
| 120 | `AI` | `人工智能` |
| 125 | `OpenAi` | `人工智能` before private qualification |
| 130 | `NetEaseMusic` | `网易音乐` |
| 140 | `Epic` | `游戏平台` |
| 145 | `Origin` | `游戏平台` |
| 150 | `Sony` | `游戏平台` |
| 155 | `Steam` | `游戏平台` |
| 160 | `Nintendo` | `游戏平台` |
| 170 | `YouTube` | `油管视频` |
| 180 | `Netflix` | `奈飞视频` |
| 190 | `Bahamut` | `巴哈姆特` |
| 200 | `BilibiliHMT` | `哔哩哔哩` |
| 210 | `Bilibili` | `哔哩哔哩` |
| 220 | `ChinaMedia` | `国内媒体` |
| 230 | `ProxyMedia` | `国外媒体` |
| 800 | `ProxyGFWlist` | `节点选择` |
| 900 | `ChinaDomain` | `全球直连` |
| 910 | `ChinaCompanyIp` | `全球直连` |
| 915 | `Download` | `全球直连` |
| 920 | `GEOIP,CN` | `全球直连` |
| final | `MATCH` | `漏网之鱼` |

AI qualification intentionally inserts three protected service routes immediately before generic `AI`; that is the sole routing-semantic extension described below.

## ACL4SSR policy groups

The public `节点选择` policy keeps the upstream order:

```text
自动选择
香港节点
台湾节点
新加坡节点
日本节点
美国节点
韩国节点
手动切换
DIRECT
```

`自动选择`, `手动切换`, country selectors, and `奈飞节点` are provider-backed selectors over the same internal general inventory. Their production regexes and ordering are declared in `rules/acl4ssr.yaml` from the pinned Full configuration rather than inferred by clash-relay.

Other semantic groups such as `全球直连`, `广告拦截`, `应用净化`, Microsoft groups, `电报消息`, `游戏平台`, media groups, and `漏网之鱼` preserve their upstream member order. They may be hidden from the FlClash top level, but rules still target the semantic groups themselves.

## Presentation-only containers

To keep FlClash compact, production exposes these top-level groups:

```text
节点选择
人工智能
流媒体
国内服务
更多策略
```

`流媒体`, `国内服务`, and `更多策略` are presentation-only containers. They are not ACL4SSR rule targets.

```text
流媒体
├─ 油管视频
├─ 奈飞视频
├─ 巴哈姆特
├─ 哔哩哔哩
├─ 国内媒体
└─ 国外媒体

国内服务
├─ 谷歌FCM
├─ 微软Bing
├─ 微软云盘
├─ 微软服务
├─ 苹果服务
├─ 游戏平台
└─ 网易音乐

更多策略
├─ 电报消息
├─ 全球直连
├─ 广告拦截
├─ 应用净化
└─ 漏网之鱼
```

The validator permits a human-readable hidden semantic group only when it remains reachable from a visible presentation path. Internal `__CR_*` groups keep their separate safety constraints.

## AI live qualification: the explicit exception

ACL4SSR remains the source of AI domain rules. Production then performs an explicit, private service-aware extension:

1. candidate nodes are divided into SG / JP / US / HK / TW / KR / OTHER inventories;
2. every candidate is tested independently against OpenAI, Claude, and Gemini through temporary Mihomo;
3. country AI providers retain the union of nodes that passed at least one protected service;
4. OpenAI traffic uses only OpenAI-qualified nodes;
5. Claude traffic uses only Claude-qualified nodes;
6. Gemini traffic uses only Gemini-qualified nodes.

After qualification, the effective protected AI prefix is:

```yaml
rules:
  - RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI
  - RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE
  - RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI
  - RULE-SET,acl4ssr_ai,人工智能
```

`acl4ssr_openai` is the pinned upstream OpenAI provider. `cr_ai_rules_claude` and `cr_ai_rules_gemini` are exact subsets of the pinned upstream `AI.list`. Qualification verifies that every expected subset entry is still present; drift fails closed.

AI country groups are hidden from the top level and nested only under `人工智能`. Empty country groups are removed. A protected service with no qualified nodes fails closed to `REJECT`; if all protected services are empty, publication aborts and the previous KV value remains untouched.

## Subscription admission is not routing

`subscriptions.yaml` may restrict node admission with fields such as `max_node_multiplier` and `allowed_uses`. These affect which inventory a node can enter.

Canonical production does **not** translate those declarations into non-AI ACL4SSR source exclusions. There are no canonical `excluded_sources` or `final_excluded_sources` declarations. Once a node is admitted to the general inventory, non-AI traffic follows the ACL4SSR policy graph above.

The generic `apply_acl4ssr_source_exclusions()` engine capability remains covered by isolated fixture tests for reuse, but canonical production deliberately does not invoke it with any exclusions.

## Standalone rule-provider model

Each enabled ACL4SSR fragment receives a deterministic provider name, for example:

```yaml
rule-providers:
  acl4ssr_bilibili:
    type: inline
    behavior: classical
    payload:
      - DOMAIN-SUFFIX,bilibili.com

rules:
  - RULE-SET,acl4ssr_bilibili,哔哩哔哩
```

The real payload is fetched from the exact pinned commit. Rule providers contain no runtime `url` or `path` and the private generated YAML includes ACL4SSR attribution.

## Validation contract

Changes to the canonical rule graph must pass all of the following before production publication:

- schema validation;
- Ruff and Python 3.11/3.12 unit tests;
- repository safety audit;
- deterministic byte-for-byte fictional generation;
- real fetch of the canonical ACL4SSR pin with exactly nine same-pin, upstream-verified compatibility omissions and zero unverified legacy rules;
- Mihomo v1.19.30 configuration/startup integration;
- Mihomo v1.19.29 configuration/startup integration;
- private AI qualification;
- final validation of the exact qualified candidate before KV publication.

Any failure before publication leaves the previous Cloudflare KV value unchanged.

## Licensing and attribution

ACL4SSR is distributed under **CC-BY-SA-4.0**. The repository does not vendor the bulk upstream rule lists into its MIT-licensed source tree. Generated profiles include attribution with the exact upstream commit and license reference.
