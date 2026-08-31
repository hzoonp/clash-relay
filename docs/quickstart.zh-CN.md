# Fork 快速上手

本指南用于把一个全新的 Fork 从私密订阅 URL 配置到可供 Mihomo / FlClash 使用的生产配置，同时确保任何带凭据的最终配置都不会提交到 GitHub。

## 1. Fork 仓库

Fork `hzoonp/clash-relay` 到自己的 GitHub 账号。

不要把真实订阅 URL 写入受版本控制的 YAML、Workflow 参数、Issue、PR、commit message 或 README。公开的 `subscriptions.yaml` 只保存 Secret 名称和来源权限；真正生成的 `config.yaml` 属于私密运行时产物，不能提交进仓库。

## 2. 创建订阅 Secret

进入 Fork：

`Settings -> Secrets and variables -> Actions -> Secrets`

创建 Repository Secret：

```text
CLASH_RELAY_SUBSCRIPTIONS
```

值使用 JSON：

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

只在 GitHub Secret 界面内把 `.invalid` 示例替换成自己的私密订阅 URL。

默认来源权限故意不是完全对称的：

```text
subscription_1 -> 仅 browsing + AI
subscription_2 -> general + browsing + AI
subscription_3 -> general + browsing + AI
subscription_4 -> general + browsing + AI
```

对于 `subscription_1`，明确倍率 `>2x` 的节点会在分类和去重前删除；恰好 `2x` 允许，没有明确倍率标记的节点保留。

## 3. 配置私有 Cloudflare Workers KV

在 Cloudflare 账号中创建一个私有 Workers KV namespace，然后回到 GitHub Fork 配置 Actions。

Repository Secret：

```text
CLOUDFLARE_API_TOKEN
```

Repository Variables：

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

API Token 只授予读写目标 Workers KV namespace 所需的最小权限。不要把 Token、Account ID、namespace ID 或订阅 URL 放进受版本控制的文件。

生产配置使用 `config.yaml` 中指定的 key，当前为 `production-config`。

## 4. 第一次先做 dry-run

打开：

`Actions -> Generate, validate, and publish -> Run workflow`

保持：

```text
publish = false
```

这是默认值。手动运行且 `publish=false` 时，会执行完整私密构建和验证链，但**不会**替换生产 KV。

成功 dry-run 表示 candidate 已通过相关生产门，包括：

```text
订阅获取 / 解析
来源权限准入
source-to-scenario 可达性审计
网页浏览实时资格检测
AI 服务级资格检测
资格处理后的二次可达性审计
Mihomo v1.19.30 验证
Mihomo v1.19.29 验证
最终聚合 production proof
```

GitHub Actions Summary 只显示聚合数据。节点名、server、凭据、订阅 URL 和最终 candidate 都不会上传成 GitHub Artifact。

## 5. 正式发布

Dry-run 成功后，再运行相同 Workflow，并设置：

```text
publish = true
```

当 `main` 上的生产输入发生变化时，push 也会自动进入同一套严格验证发布链。

如果新的 candidate 与当前生产值不同，Workflow 会在替换前先把当前已验证 bytes 保存到私有 recovery slot。只有 source audit、实时 qualification 和两个固定 Mihomo 版本全部通过后，新的 candidate 才会写入 `production-config`。

## 6. 网页浏览调度规则

Browsing inventory 使用三次实时探测：

```text
3/3 成功 -> stable -> 自动网页浏览候选
2/3 成功 -> reserve -> 仅保留给手动网页浏览选择
0/3 或 1/3 -> 从 browsing inventory 剔除
```

如果 stable 节点不足 3 个，自动组会安全退回完整 qualified browsing provider，避免把自动池削得过薄。

运行时 `url-test` 继续负责实时延迟选择和 tolerance 防抖。单纯因为 RTT 较高不会删除节点。

### 匿名历史状态

跨 Workflow 的历史记录可以把“当前 3/3 仍然成功、但长期稳定性已经明显较差”的节点从 automatic stable tier 降级；历史记录**永远不会**把当前实时资格检测失败的节点重新提升回来。

历史数据保存在私有 Workers KV 中，仅保存 HMAC-SHA256 fingerprint 和聚合稳定性字段，不保存 runtime 节点名、server、凭据或订阅 URL。历史缺失、格式错误或暂时读取失败时，会退回纯当前运行的实时调度，不因此阻断生产配置。

## 7. AI 调度规则

OpenAI、Claude、Gemini 会分别通过临时 Mihomo 实际资格检测。同一个节点可能只通过其中一个服务。

三个服务拥有独立 qualified routing graph。如果某一个服务没有任何合格节点，只对该服务 fail closed，而不会借用未经该服务验证的节点；其他 AI 服务只要仍有合格节点就可以继续工作。

## 8. 查看 Production proof

Dry-run 或 publish 成功后，在 GitHub Actions Summary 查看最终 production proof。它只包含聚合信息，例如：

```text
candidate 字节数与 SHA-256
source reachability 状态
browsing tested / qualified / stable / reserve / rejected
各 AI 服务 qualified 数量
通过验证的 Mihomo core 版本
publication = dry-run 或 published
```

可以用 SHA-256 标识“到底是哪一份精确 candidate 通过验证并发布”，而不暴露 candidate 内容。

## 9. 回滚生产配置

每次成功发布一份**不同于当前生产值**的新 candidate 前，Workflow 都会先保存当前 production bytes 作为私有 previous-good recovery slot。

需要回滚时打开：

`Actions -> Roll back production config -> Run workflow`

设置：

```text
confirm = true
```

Rollback 只允许手动执行，并且只允许在 `main` 上执行。Workflow 会先从私有 KV 取出 previous bytes，再用 Mihomo v1.19.30 和 v1.19.29 重新验证；两个 core 都通过后才会重新激活为 `production-config`。

不要把 rollback 当作解决“构建失败”的办法。新的 candidate 只要任一 gate 失败，本来就不会覆盖当前生产配置。

## 故障排查

### 订阅获取步骤失败

检查 `CLASH_RELAY_SUBSCRIPTIONS` 是否为有效 JSON，并包含 `subscriptions.yaml` 中所有启用来源要求的 Secret 名称。真实 URL 仍然只放在 Secret 界面。

### Browsing qualification 没有留下可用 provider

Workflow 会 fail closed 并保留原生产 KV。检查订阅可用性，以及 probe endpoint 是否能通过候选节点访问。不要为了强行发布而绕过 2/3 的资格边界。

### 某个 AI 服务 qualified 数量为 0

该服务会按设计 fail closed。OpenAI、Claude、Gemini 是独立判断，一个服务为 0 不代表另外两个服务也不可用。

### Mihomo 拒绝 candidate

公开 Workflow 故意不输出可能含凭据的详细 core 日志。应修复结构异常的订阅节点或项目配置，而不是跳过两个固定 Mihomo 版本的生产验证。

### Cloudflare 发布失败

检查 `CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_KV_NAMESPACE_TITLE`，并确认 Token 有权读写目标 Workers KV namespace。发布失败不会主动删除已经存在的生产值。

### Scheduler history 读取失败

History 是辅助信息。Workflow 会退回当前运行的实时 browsing qualification。临时读取失败时，该次运行也不会覆盖未知的旧 history。

### Rollback 提示没有 previous config

只有“成功的新发布替换了一份不同的旧生产配置”后才会存在 previous slot。首次发布、或 candidate bytes 与当前生产完全一致时，不会创建/覆盖这个 recovery point。

## 安全检查清单

正式使用 Fork 前确认：

- 真实订阅 URL 只存在于 GitHub Secrets。
- 从私密节点生成的 `config.yaml` 从不 commit，也不上传到 Artifact / Release / Gist。
- 除非你明确重新设计来源权限，否则 `subscription_1` 仍然只能进入 `browsing` 和 `ai`。
- 最终 `MATCH` 仍位于 general graph。
- 手动发布默认 `publish=false`。
- Rollback 必须明确 `confirm=true`，且重新通过两个 Mihomo core。
- Cloudflare 凭据只拥有私有 KV 所需的最小权限。
