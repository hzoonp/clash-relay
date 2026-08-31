# clash-relay

[English](README.md)

`clash-relay` 是一个面向 Mihomo / FlClash 的确定性配置生成与场景调度项目。多个私密订阅在 GitHub Actions 中获取、清洗、分类和合并，最终生成一个 standalone `config.yaml`。公开仓库只保存策略元数据；真实订阅 URL 和最终节点凭据不会进入 Git 历史。

> **敏感信息提示**：最终 `config.yaml` 内联真实节点凭据，应视为最高敏感数据。生产工作流只把通过验证的配置写入私有 Cloudflare Workers KV，不上传到公开 Artifact、Release、Gist、commit 或 Pages。

## 当前生产场景

生产配置把“合并多个订阅”与“允许所有订阅用于所有业务”严格分开：

```text
SUBSCRIPTION_1_URL ──> 倍率过滤(>2x 删除) ──┬─> 网页浏览
                                           └─> 人工智能

SUBSCRIPTION_2_URL ─────────────────────────┬─> 通用应用
SUBSCRIPTION_3_URL ─────────────────────────┼─> 网页浏览
SUBSCRIPTION_4_URL ─────────────────────────┴─> 人工智能
```

核心不变量：

1. `subscription_1` 只允许 `browsing` 和 `ai`，**不能进入 `general`**。
2. `subscription_1` 中明确标注倍率 `> 2x` 的节点在分类、去重和 provider 生成前直接剔除。
3. `2x` 本身允许；没有明确倍率标记的节点保留，不猜测倍率。
4. YouTube、Netflix、Telegram、游戏平台、微软服务、媒体组、下载规则和最终 `MATCH` 等非网页/非 AI 流量只使用 `general` inventory，因此无法选到 `subscription_1`。
5. 通用 `ProxyGFWlist` 进入独立的 `网页浏览` inventory；AI 进入独立 AI inventory 并继续做服务资格检测。
6. 最终 `MATCH` 保持 `漏网之鱼 -> general`。未分类流量不能因为“可能来自浏览器”而获得 `subscription_1` 权限。

这里不使用浏览器进程名做安全边界，因为 Android、iOS、Windows、macOS 和不同 FlClash/Mihomo 运行方式下进程识别并不一致。生产边界由规则目标和节点池权限共同保证。

## 数据流

```text
GitHub Secrets
  └─ CLASH_RELAY_SUBSCRIPTIONS
          ↓
获取多个订阅
          ↓
安全解析 / 节点标准化
          ↓
订阅级准入
  ├─ subscription_1: >2x 删除
  └─ allowed_uses 强制边界
          ↓
去重 / 国家分类
          ↓
三个逻辑库存
  ├─ general   : subscription_2+
  ├─ browsing  : subscription_1+
  └─ ai        : subscription_1+
          ↓
固定 ACL4SSR 规则 + 场景调度
          ↓
OpenAI / Claude / Gemini 实测资格
          ↓
Mihomo v1.19.30 + v1.19.29 验证
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

## 订阅配置

公开的 `subscriptions.yaml` 不包含 URL。生产中的关键配置为：

```yaml
subscriptions:
  - id: subscription_1
    secret_name: SUBSCRIPTION_1_URL
    allowed_uses: [browsing, ai]
    max_node_multiplier: 2.0

  - id: subscription_2
    secret_name: SUBSCRIPTION_2_URL
    allowed_uses: [general, browsing, ai]
```

`subscription_3`、`subscription_4` 与订阅 2 使用相同的生产权限模型。

倍率过滤识别常见显式格式，例如：

```text
香港 2x       -> 保留
日本 x2.0     -> 保留
美国 2.01x    -> 删除
新加坡 3倍    -> 删除
倍率: 4       -> 删除
普通节点      -> 保留
```

如果节点名中出现多个倍率标记，按解析到的最高显式倍率判断。

## 场景节点池

`policies.yaml` 使用不同 `source_use` 建立硬边界：

```text
general
  source_use: general
  subscription_1: 不允许

browsing
  source_use: browsing
  subscription_1: 允许

ai_*
  source_use: ai
  subscription_1: 允许
```

选择器在生成 provider 前检查 `Node.source_allowed_uses`。因此即使订阅 1 的节点延迟最低，也不会因为 url-test、fallback、手动选择或去重而泄漏到 `general`。

## 路由策略

`rules/acl4ssr.yaml` 固定 `ACL4SSR/ACL4SSR` 到不可变 commit：

```text
c498ae4911f15b19c5ceaef6f8737ca8705b4430
```

大部分应用规则继续保持 ACL4SSR Full 的独立策略目标。生产有两个明确的调度扩展：

```text
ProxyGFWlist -> 网页浏览 -> browsing inventory
AI/OpenAI    -> 人工智能 -> AI inventories
```

典型其他规则仍走 general 体系：

```text
Telegram                     -> 电报消息
YouTube                      -> 油管视频
Netflix                      -> 奈飞视频
Epic/Origin/Sony/Steam/...   -> 游戏平台
ChinaMedia                   -> 国内媒体
ProxyMedia                   -> 国外媒体
Download                     -> 全球直连
MATCH                        -> 漏网之鱼
```

因此 `subscription_1` 不会出现在这些非授权场景的候选 provider 中。

## AI 实测资格

AI 候选节点按名称确定性分类到 SG / JP / US / HK / TW / KR / OTHER。可信 Runner 启动临时 Mihomo，并通过候选节点实际探测：

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

OpenAI、Claude、Gemini 独立判定。网络错误、timeout、TLS 失败或不允许的状态码都会让该节点在对应服务上失败。受保护服务没有合格节点时 fail closed，而不是回退到未经验证节点。

## GitHub Secrets

推荐只保存一个订阅映射 Secret：

```text
CLASH_RELAY_SUBSCRIPTIONS
```

值可以是 JSON：

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

发布到 Cloudflare KV 还需要：

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

不要把真实订阅 URL 写入 `subscriptions.yaml`、README、Actions 参数或日志。

## 验证

核心策略由测试直接锁定：

```text
subscription_1 / 1x       -> 保留
subscription_1 / 2x       -> 保留
subscription_1 / 2.01x    -> 删除
subscription_1 / 无倍率   -> 保留

subscription_1 -> general  -> 禁止
subscription_1 -> browsing -> 允许
subscription_1 -> ai       -> 允许

ProxyGFWlist -> 网页浏览
YouTube/Netflix/Game/Download/MATCH -> 非网页浏览池
```

CI 在 Python 3.11/3.12 上运行 Ruff、单元测试、安全审计、确定性生成，并使用两个稳定 Mihomo 版本执行真实配置/启动集成测试。

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

## 文档

- [配置模型](docs/configuration.md)
- [架构](docs/architecture.md)
- [ACL4SSR 规则模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
