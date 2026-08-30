# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic, fail-closed Mihomo configuration builder designed to run safely from a **public GitHub repository**. Public YAML contains policy and subscription metadata only. Real subscription URLs stay in GitHub Actions Secrets, generated node credentials exist only on an ephemeral GitHub-hosted runner, and the qualified and validated standalone `config.yaml` is published only to private Cloudflare Workers KV.

FlClash consumes the token-protected Worker URL. It does not need runtime access to GitHub or ACL4SSR.

> **Credential warning**
>
> The generated configuration contains inline proxy credentials and must be treated as highest-sensitivity data. The supported public production workflow does **not** upload it to Actions Artifacts, Releases, Gists, commits, or Pages.

## Production contract

Production has one intentionally narrow contract:

1. **ACL4SSR owns all non-AI routing behavior.** Rule order, rule targets, policy membership, and policy default order mirror the pinned `ACL4SSR_Online_Full.ini` semantics.
2. **FlClash presentation may differ without changing routing.** Semantic ACL4SSR groups may be hidden and nested under compact presentation-only containers, but those containers are never rule targets.
3. **AI live qualification is the only routing-semantic extension.** OpenAI, Claude, and Gemini are tested through real candidate nodes, and protected service traffic is routed only through nodes that passed that service's probe.
4. **No local rule prelude is allowed ahead of ACL4SSR.** Canonical `rules/direct.yaml` is intentionally empty.
5. **Subscription policy is admission policy, not a hidden routing rewrite.** Multiplier limits and `allowed_uses` may decide whether a node enters an inventory, but canonical non-AI ACL4SSR routes do not exclude a subscription source after admission.

The production flow is:

```text
4 private subscription URLs
          ↓
subscription/node admission
          ↓
internal general inventory
          ↓
pinned ACL4SSR Online Full semantics
          ↓
AI candidate countries + live OpenAI / Claude / Gemini qualification
          ↓
service-qualified hidden AI routes
          ↓
Mihomo v1.19.30 + v1.19.29
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

Production enables only:

```yaml
modules:
  general: true
```

`services.yaml` is empty. `policies.yaml` contains one internal general inventory plus seven AI country candidate inventories. Generic engine features continue to be tested under `tests/fixtures/project/` and are not automatically exposed in production.

## Strict ACL4SSR routing

`rules/acl4ssr.yaml` pins `ACL4SSR/ACL4SSR` at immutable commit:

```text
c498ae4911f15b19c5ceaef6f8737ca8705b4430
```

The canonical manifest restores the upstream Full policy mapping instead of collapsing unrelated applications into a few routing groups. Examples:

```text
LocalAreaNetwork / UnBan / GoogleCN / SteamCN  -> 全球直连
BanAD                                         -> 广告拦截
BanProgramAD                                  -> 应用净化
GoogleFCM                                     -> 谷歌FCM
Bing / OneDrive / Microsoft                   -> their own Microsoft groups
Apple                                         -> 苹果服务
Telegram                                      -> 电报消息
YouTube                                       -> 油管视频
Netflix                                       -> 奈飞视频
Bahamut                                       -> 巴哈姆特
BilibiliHMT / Bilibili                        -> 哔哩哔哩
ChinaMedia                                    -> 国内媒体
ProxyMedia                                    -> 国外媒体
ProxyGFWlist                                  -> 节点选择
ChinaDomain / ChinaCompanyIp / Download       -> 全球直连
GEOIP,CN                                      -> 全球直连
MATCH                                         -> 漏网之鱼
```

The original ACL4SSR selector behavior is also retained, including `节点选择`, `自动选择`, `手动切换`, country selectors, `奈飞节点`, `全球直连`, `广告拦截`, `应用净化`, application/media selectors, and `漏网之鱼`. Provider-backed selectors reuse the private inline general inventory; no node credentials are copied merely to reproduce an ACL4SSR selector.

Rule fragments are fetched during the trusted build and embedded as inline classical Mihomo rule providers. The final profile is standalone: generated rule providers contain no runtime `url` or `path`.

The adapter counts legacy rule types that pinned Mihomo cannot express. CI additionally fetches the canonical pinned Full sources and requires **zero skipped legacy rules** for this production pin. A future pin that would require silently dropping rules is therefore rejected rather than called “strict”.

See [Routing rules and ACL4SSR](docs/rules.md).

## FlClash presentation

Presentation is deliberately compact while routing remains ACL4SSR-compatible. The intended top-level groups are:

```text
节点选择
人工智能
流媒体
国内服务
更多策略
```

`流媒体`, `国内服务`, and `更多策略` are **presentation-only containers**. No ACL4SSR rule targets them. They expose hidden semantic groups so a user can still reach the original policy when a manual override is needed.

For example:

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

Hiding or nesting a semantic group is a UI decision only; its ACL4SSR rule target and member order remain unchanged.

## AI qualification and country groups

AI candidates are deterministically classified from **node names**, not GeoIP. Production recognizes SG, JP, US, HK, TW, and KR from common location labels; unknown or ambiguous labels go to `OTHER`.

On trusted `main`, short-lived Mihomo processes pin each candidate node and send actual `HEAD` requests through that node to:

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

The services are qualified independently. A node is accepted for a service only when the probe returns the configured accepted status range; production currently requires `200-399`. Network errors, timeout, TLS failure, or an unaccepted HTTP status fail that node for that service.

The AI country providers retain the union of nodes that qualify for at least one protected service. Hidden service-specific routes then enforce:

```text
OpenAI traffic -> OpenAI-qualified nodes only
Claude traffic -> Claude-qualified nodes only
Gemini traffic -> Gemini-qualified nodes only
```

The pinned ACL4SSR OpenAI provider and exact Claude/Gemini subsets derived from pinned `AI.list` are placed immediately before the generic ACL4SSR AI rule. The generic AI rule itself remains present for the rest of ACL4SSR AI coverage.

AI country groups are hidden from the top level and are exposed only under `人工智能`:

```text
人工智能
├─ AI · 新加坡
├─ AI · 日本
├─ AI · 美国
├─ AI · 香港
├─ AI · 台湾
├─ AI · 韩国
├─ AI · 其他地区
└─ DIRECT
```

Empty countries are removed. If one protected service has no qualified node, only that service fails closed to `REJECT`. If no node qualifies for any protected service, publication fails closed and the previous KV value is retained.

Public Actions logs contain only aggregate qualification counts; node names, servers, credentials, and per-node results are not printed.

## Subscription admission

A subscription may declare `max_node_multiplier`. The filter recognizes explicit node-name markers such as `2x`, `x2.5`, `3倍`, or `倍率:4`. Nodes explicitly above the configured ceiling are removed before provider generation; unmarked nodes are retained rather than guessed.

`allowed_uses` controls which inventory a subscription may enter. In canonical production, this is **not** converted into application-specific source exclusions inside ACL4SSR routing. Once a node is admitted to the general inventory, non-AI routing follows ACL4SSR exactly. AI remains the explicit exception because its candidate inventory and live qualification are part of the project's AI scheduling feature.

## Generated AI rule shape

Before qualification, ACL4SSR remains the source of AI rule data. Private qualification then introduces only the protected service routes:

```yaml
rules:
  - RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI
  - RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE
  - RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI
  - RULE-SET,acl4ssr_ai,人工智能
  # ...remaining pinned ACL4SSR order...
  - GEOIP,CN,全球直连,no-resolve
  - MATCH,漏网之鱼
```

`cr_ai_rules_claude` and `cr_ai_rules_gemini` are exact subsets of the already-pinned ACL4SSR `AI.list`. If the pinned payload no longer contains the expected service rules, qualification fails closed for review.

## Security architecture

```text
Public GitHub repository
  ├─ config.yaml / subscriptions.yaml       public metadata only
  ├─ CLASH_RELAY_SUBSCRIPTIONS              GitHub Secret
  └─ CLOUDFLARE_API_TOKEN                   GitHub Secret
            ↓
      trusted main Actions
            ↓
      mask each subscription URL
            ↓
      fetch / parse / admission / deduplicate
            ↓
      fetch pinned ACL4SSR fragments
            ↓
      generate private standalone YAML
            ↓
      live per-service AI qualification
            ↓
      validate exact candidate with both Mihomo pins
            ↓
      Cloudflare Workers KV
            ↓
      token-protected Worker URL
            ↓
           FlClash
```

The Cloudflare publication gate refuses unsafe public publishing modes. If generation, AI qualification, or either Mihomo validation fails, KV is not updated.

## GitHub configuration

Repository **Secrets**:

```text
CLASH_RELAY_SUBSCRIPTIONS
CLOUDFLARE_API_TOKEN
```

Repository **Variables**:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

`CLASH_RELAY_SUBSCRIPTIONS` is a JSON or YAML mapping from each tracked `secret_name` to its private subscription URL. `PROFILE_TOKEN` protects the Worker profile URL and must not be stored in GitHub.

## Local development

Python 3.11 or 3.12:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/repository_audit.py
```

CI additionally performs byte-for-byte deterministic generation and real Mihomo v1.19.30 / v1.19.29 integration validation.

Further documentation:

- [Configuration](docs/configuration.md)
- [Routing rules and ACL4SSR](docs/rules.md)
- [Security model](docs/security.md)
- [Publishing](docs/publishing.md)
- [First release checklist](docs/release-checklist.md)

## License

MIT
