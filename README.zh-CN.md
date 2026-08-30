# clash-relay

[English](README.md)

`clash-relay` 是一个确定性、fail-closed 的 Mihomo 配置生成项目，目标是在 **Public GitHub 仓库** 中安全生成真实生产配置。公开 YAML 只保存策略和订阅元数据；真实订阅 URL 只进入 GitHub Actions Secrets；包含节点凭据的最终 `config.yaml` 只在临时 GitHub Runner 上生成，并在通过 AI 资格筛选和两个 Mihomo 稳定版验证后直接写入私有 Cloudflare Workers KV。

最终配置是标准、单文件、standalone Mihomo YAML。FlClash 只需要受 `PROFILE_TOKEN` 保护的 Worker URL，不需要在运行时访问 GitHub 或 ACL4SSR。

> **敏感信息提示**：最终 `config.yaml` 内联真实节点凭据，应视为最高敏感数据。支持的 Public 生产工作流不会把它上传到 Actions Artifact、Release、Gist、Git commit 或 Pages。

## 生产契约

canonical 生产配置遵循一个明确边界：

1. **除 AI 实测调度外，所有规则行为由 ACL4SSR 决定。** 规则顺序、规则目标、策略成员和默认成员顺序严格跟随固定版本 `ACL4SSR_Online_Full.ini` 的语义。
2. **FlClash 可以优化展示，但不能改变路由。** ACL4SSR 的语义策略组可以隐藏并嵌套到更简洁的展示容器中，但这些展示容器绝不能成为规则 target。
3. **AI 实测资格是唯一允许改变路由语义的扩展。** OpenAI、Claude、Gemini 会通过真实候选节点探测，受保护的服务流量只能使用对应服务实测通过的节点。
4. **ACL4SSR 之前不允许私加本地规则。** canonical `rules/direct.yaml` 有意保持空规则集。
5. **订阅策略只负责节点准入，不再暗中改写 ACL4SSR 路由。** 倍率限制和 `allowed_uses` 可以决定节点能否进入某个库存，但节点进入通用库存后，非 AI 路由不再按订阅源做 application-specific exclusion。

生产数据流：

```text
4 个私密订阅 URL
          ↓
订阅 / 节点准入
          ↓
内部通用节点库存
          ↓
固定版本 ACL4SSR Online Full 语义
          ↓
AI 国家候选池 + OpenAI / Claude / Gemini 实测资格
          ↓
隐藏的服务专用合格节点路由
          ↓
Mihomo v1.19.30 + v1.19.29
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

生产仍只启用：

```yaml
modules:
  general: true
```

`services.yaml` 为空。`policies.yaml` 只保留一个内部通用节点库存和 7 个 AI 国家/地区候选库存。通用引擎的其它能力继续在 `tests/fixtures/project/` 中独立测试，不会自动进入真实生产配置。

## 严格 ACL4SSR 路由

`rules/acl4ssr.yaml` 固定 `ACL4SSR/ACL4SSR` 到不可变 commit：

```text
c498ae4911f15b19c5ceaef6f8737ca8705b4430
```

canonical manifest 已恢复 ACL4SSR Full 原本的策略映射，不再为了减少 FlClash 顶层组数把不同应用合并到同一个路由目标。典型映射为：

```text
LocalAreaNetwork / UnBan / GoogleCN / SteamCN  -> 全球直连
BanAD                                         -> 广告拦截
BanProgramAD                                  -> 应用净化
GoogleFCM                                     -> 谷歌FCM
Bing / OneDrive / Microsoft                   -> 各自微软策略组
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

ACL4SSR 原本的策略行为也保留，包括 `节点选择`、`自动选择`、`手动切换`、各国家节点、`奈飞节点`、`全球直连`、`广告拦截`、`应用净化`、应用/媒体策略和 `漏网之鱼` 的成员及顺序。provider-backed selector 直接复用私密的通用 inline provider，不会仅为了复刻 ACL4SSR selector 而复制一份节点凭据。

ACL4SSR 规则片段在可信构建阶段获取，并内联为 Mihomo classical rule-provider。最终 YAML 为 standalone，运行时 rule-provider 不含 `url` 或 `path`。

adapter 会统计固定 Mihomo 无法表达的 legacy 规则类型；CI 还会真实抓取 canonical 固定 Full 片段并强制要求 **`skipped_legacy_rules == 0`**。以后若更新 ACL4SSR pin 后必须丢规则才能生成，CI 会失败，而不是把不完整结果继续称为“严格遵循”。

详见 [ACL4SSR 规则模型](docs/rules.md)。

## FlClash 展示层

展示层尽量简洁，但规则行为不合并。预期顶层只显示：

```text
节点选择
人工智能
流媒体
国内服务
更多策略
```

其中 `流媒体`、`国内服务`、`更多策略` 都是**纯展示容器**，没有任何 ACL4SSR 规则会直接命中它们。真正被规则命中的 ACL4SSR 语义组隐藏在容器下，用户需要手动覆盖时仍然可以进入对应策略。

例如：

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

因此“隐藏/嵌套”只改变 FlClash UI，不改变 ACL4SSR 的 rule target、成员顺序或默认行为。

## AI 实测资格与国家分组

AI 候选节点根据**节点名称**做确定性国家/地区分类，不使用 GeoIP 猜测出口位置。生产识别 SG、JP、US、HK、TW、KR，无法可靠识别时进入 `OTHER`。

可信 `main` Runner 会启动临时 Mihomo，把 selector 固定到具体候选节点，再通过该节点实际发送 `HEAD` 请求：

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

三个服务独立判定资格。当前生产要求对应 probe 返回 `200-399`；网络错误、timeout、TLS 失败或不允许的 HTTP 状态，只让该节点在该服务上失败。

最终 AI 国家 provider 保存至少通过 OpenAI、Claude、Gemini 任一服务的节点并集，再由隐藏服务路由严格限制：

```text
OpenAI 流量 -> 仅 OpenAI 实测通过节点
Claude 流量 -> 仅 Claude 实测通过节点
Gemini 流量 -> 仅 Gemini 实测通过节点
```

固定 ACL4SSR `OpenAi.list` 以及从固定 `AI.list` 精确派生的 Claude/Gemini 子集，会放在通用 ACL4SSR AI 规则之前。除这三个受保护服务的实测调度外，其余 AI 域名仍由固定 ACL4SSR AI 规则覆盖。

AI 国家组不在顶层展示，只在 `人工智能` 内出现：

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

空国家组会被删除；某单一服务没有任何合格节点时，仅该服务 fail closed 到 `REJECT`；三个受保护服务全部没有合格节点时，整个发布 fail closed，不覆盖上一版 Cloudflare KV。

公开 Actions 日志只输出聚合资格统计，不输出节点名称、服务器、凭据或单节点结果。

## 订阅节点准入

订阅可以声明 `max_node_multiplier`。过滤器只识别节点名称里明确写出的倍率，例如 `2x`、`x2.5`、`3倍`、`倍率:4`。明确高于上限的节点会在 provider 生成前剔除；没有明确倍率标记的节点保留，不猜测。

`allowed_uses` 只决定订阅能进入哪些节点库存。canonical 生产**不会再把它转换为 ACL4SSR 应用路由中的 source exclusion**。节点一旦进入通用库存，所有非 AI 流量严格按 ACL4SSR 路由。AI 是唯一例外，因为 AI candidate inventory 和实时资格筛选本身就是项目明确提供的调度功能。

## 最终 AI 规则形态

资格筛选之前，AI 规则数据仍来自 ACL4SSR。私密资格阶段只增加受保护服务的专用路由：

```yaml
rules:
  - RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI
  - RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE
  - RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI
  - RULE-SET,acl4ssr_ai,人工智能
  # ...其余规则继续保持固定 ACL4SSR 顺序...
  - GEOIP,CN,全球直连,no-resolve
  - MATCH,漏网之鱼
```

`cr_ai_rules_claude` 和 `cr_ai_rules_gemini` 是固定 ACL4SSR `AI.list` 的精确子集；如果 pin 中相关规则漂移，qualification 会 fail closed 并要求人工审查。

## 安全发布链

```text
Public GitHub
  ├─ config.yaml / subscriptions.yaml      公开元数据
  ├─ CLASH_RELAY_SUBSCRIPTIONS             GitHub Secret
  └─ CLOUDFLARE_API_TOKEN                  GitHub Secret
             ↓
      trusted main Actions
             ↓
      mask 每个订阅 URL
             ↓
      获取 / 解析 / 准入 / 去重
             ↓
      获取固定 ACL4SSR 片段
             ↓
      生成私密 standalone YAML
             ↓
      AI 逐服务真实资格筛选
             ↓
      两版 Mihomo 验证 exact candidate
             ↓
      Cloudflare Workers KV
             ↓
      受 token 保护的 Worker URL
             ↓
            FlClash
```

Cloudflare publication gate 会拒绝不安全的公开发布方式。任何生成、AI qualification 或 Mihomo 验证失败，都不会覆盖 KV 中上一版成功配置。

## GitHub 配置

Repository **Secrets**：

```text
CLASH_RELAY_SUBSCRIPTIONS
CLOUDFLARE_API_TOKEN
```

Repository **Variables**：

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

`CLASH_RELAY_SUBSCRIPTIONS` 是 `subscriptions.yaml` 中各 `secret_name` 到真实订阅 URL 的 JSON/YAML 映射。`PROFILE_TOKEN` 只用于保护 Worker profile URL，不应存入 GitHub。

## 本地开发

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/repository_audit.py
```

CI 还会做字节级确定性生成，以及 Mihomo v1.19.30 / v1.19.29 的真实配置和启动集成验证。

详细说明：

- [配置模型](docs/configuration.md)
- [ACL4SSR 规则模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
