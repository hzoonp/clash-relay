# Routing rules and ACL4SSR

Production uses pinned ACL4SSR Full rule data as the base routing model, then applies two explicit scheduler boundaries owned by clash-relay:

1. generic `ProxyGFWlist` traffic is routed to a dedicated `网页浏览` inventory;
2. AI traffic is routed through live-qualified AI inventories.

All other application-specific ACL4SSR targets and the final `MATCH` path remain on the general inventory.

## Canonical source

`rules/acl4ssr.yaml` pins:

```text
repository: ACL4SSR/ACL4SSR
ref: c498ae4911f15b19c5ceaef6f8737ca8705b4430
license: CC-BY-SA-4.0
```

The build fetches selected rule fragments from that immutable commit, parses supported classical Clash rules, and embeds them as `type: inline`, `behavior: classical` Mihomo rule providers. The final private profile has no runtime dependency on GitHub or ACL4SSR.

Canonical production has no local rule prelude: `rules/direct.yaml` intentionally contains an empty rule list.

## Source-policy boundary

The important distinction is between **routing target** and **node inventory**.

```text
subscription_1
  allowed_uses: browsing, ai
  max_node_multiplier: 2.0

subscription_2+
  allowed_uses: general, browsing, ai
```

The selector checks `allowed_uses` before generating providers. Therefore `subscription_1` is structurally absent from `general` providers. No post-generation source exclusion is required to keep it out of media, games, downloads, messaging, cloud services, or the final fallback path.

## Canonical routing map

The production semantic targets are:

| Order | ACL4SSR source | Production target |
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
| 800 | `ProxyGFWlist` | `网页浏览` |
| 900 | `ChinaDomain` | `全球直连` |
| 910 | `ChinaCompanyIp` | `全球直连` |
| 915 | `Download` | `全球直连` |
| 920 | `GEOIP,CN` | `全球直连` |
| final | `MATCH` | `漏网之鱼` |

`ProxyGFWlist -> 网页浏览` is deliberate: it is the generic web route allowed to use the browsing inventory, including eligible `subscription_1` nodes.

`MATCH -> 漏网之鱼` deliberately remains general. An unmatched flow is not proven to be web browsing, so allowing it into the browsing inventory would weaken the source permission boundary.

## Browsing policy groups

The browsing path has its own provider-backed groups:

```text
网页浏览
  ├─ 网页自动   # hidden url-test over browsing inventory
  └─ DIRECT
```

The browsing inventory is generated from `source_use: browsing`. It may include subscription 1 and ordinary subscriptions that also allow browsing.

General selectors remain backed by `source_use: general`:

```text
节点选择
自动选择
手动切换
香港节点 / 台湾节点 / 新加坡节点 / 日本节点 / 美国节点 / 韩国节点
奈飞节点
```

Because subscription 1 does not allow `general`, these groups cannot expose it.

## Application-specific routing

Application rules retain dedicated ACL4SSR-style policy groups. Examples:

```text
Telegram -> 电报消息
YouTube  -> 油管视频
Netflix  -> 奈飞视频
Games    -> 游戏平台
Bilibili -> 哔哩哔哩
```

Those groups ultimately reference the general provider-backed selectors, not the browsing inventory. This is what prevents a browsing-only subscription from leaking into streaming, gaming, messaging, or media traffic.

## FlClash presentation

Mihomo proxy groups are controls, not folders. Canonical production therefore avoids unreferenced pseudo-containers.

Actionable groups remain visible, including `网页浏览`, `人工智能`, `手动切换`, application/media selectors, and `漏网之鱼`. Automatic helper groups such as `自动选择`, `网页自动`, and country `url-test` selectors may be hidden while still participating as real members.

## AI live qualification

ACL4SSR remains the source of AI domain rules. Production then performs a private service-aware extension:

1. candidate nodes are divided into SG / JP / US / HK / TW / KR / OTHER inventories;
2. every candidate is tested independently against OpenAI, Claude, and Gemini through temporary Mihomo;
3. country AI providers retain the union of nodes that passed at least one protected service;
4. OpenAI traffic uses only OpenAI-qualified nodes;
5. Claude traffic uses only Claude-qualified nodes;
6. Gemini traffic uses only Gemini-qualified nodes.

After qualification, the protected AI prefix is shaped like:

```yaml
rules:
  - RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI
  - RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE
  - RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI
  - RULE-SET,acl4ssr_ai,人工智能
```

A protected service with no qualified node fails closed to `REJECT`; if all protected services are empty, publication aborts and the previous KV value remains untouched.

## Multiplier admission

`max_node_multiplier` is applied before classification and deduplication. For canonical subscription 1:

```text
2x       accepted
2.01x    rejected
3倍      rejected
unmarked accepted
```

The filter recognizes explicit multiplier syntax only and does not infer an unmarked node's commercial billing ratio.

## No canonical source-exclusion rewrite

The generic `apply_acl4ssr_source_exclusions()` capability remains available and tested for custom projects, but canonical production does not need `excluded_sources` or `final_excluded_sources` for subscription 1 isolation.

Inventory admission is the stronger invariant:

```text
subscription_1 not admitted to general
        ↓
no general provider contains subscription_1
        ↓
no general application route can select subscription_1
```

## ACL4SSR compatibility boundary

The pinned Full lists contain nine legacy `URL-REGEX` rules that Mihomo 1.19.x cannot express as classical rules: seven in `Download.list`, one in `ChinaMedia.list`, and one in `ProxyMedia.list`.

An omission is allowed only when the exact same rule is explicitly commented out by ACL4SSR's maintained `Clash/Providers/*.yaml` representation at the same immutable commit. Canonical CI requires:

```text
verified_compatibility_omissions == 9
unverified_legacy_rules == 0
```

Any unverified compatibility drift fails closed.

## Validation contract

Changes to the canonical rule graph must pass:

- schema validation;
- source-use isolation tests;
- multiplier admission tests;
- explicit `ProxyGFWlist -> 网页浏览` routing tests;
- application-route non-browsing tests;
- Ruff and Python 3.11/3.12 unit tests;
- deterministic byte-for-byte fictional generation;
- repository safety audit;
- pinned ACL4SSR compatibility validation;
- Mihomo v1.19.30 and v1.19.29 configuration/startup integration;
- private AI qualification and final exact-candidate validation before KV publication.
