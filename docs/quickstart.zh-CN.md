# Fork 快速上手

本指南用于把一个全新的 Fork 从私密订阅 URL 配置到可供 Mihomo / FlClash 使用的生产配置，同时确保任何带凭据的最终配置都不会提交到 GitHub。

## 1. Fork 仓库

Fork `hzoonp/clash-relay` 到自己的 GitHub 账号。

不要把真实订阅 URL 写入受版本控制的 YAML、Workflow 参数、Issue、PR、commit message 或 README。公开的 `subscriptions.yaml` 只保存 Secret 名称和来源权限；真正生成的生产 `config.yaml` 属于私密运行时产物，**不能提交进仓库**。

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
网页浏览 HTTPS 实时资格检测
媒体 / 消息 UDP/QUIC transport 资格检测
AI 服务级资格检测
资格处理后的 current-policy 二次审计
tools/mihomo-versions.json 中全部 stable Mihomo core
最终聚合 production proof
```

GitHub Actions Summary 只显示聚合数据。节点名、server、凭据、订阅 URL、资格阶段文件和最终 candidate **从不 commit**，也不会上传到 **Artifact / Release / Gist**。

## 5. 正式发布

Dry-run 成功后，再运行相同 Workflow，并设置：

```text
publish = true
```

当 `main` 上的生产输入发生变化时，push 也会自动进入同一套严格验证发布链。

P17 在不改变客户端固定 key 的前提下增加私有版本化 release。最终精确 bytes 会先按 SHA-256 release ID 写入不可变 KV key 并读回校验；之后才允许激活固定的 `production-config`，最后提交 `current-release-v1` / `previous-release-v1` 指针。旧 `.previous-v1` recovery slot 继续作为迁移兼容兜底。

如果客户端固定 key 已经更新，但 release pointer 提交失败，发布层会尽力把旧 production 精确 bytes 恢复回来。Cloudflare KV 不提供多 key 原子事务，所以这里是**补偿式事务**，不会虚假宣称跨 key 原子性。

AI cache 和 browsing scheduler history 是派生优化状态，在 production release 成功提交后再保存。它们写入失败只会产生 warning，不会把已经成功且验证完成的生产发布误报成失败；后续运行会通过实时探测安全重建缺失状态。

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

跨 Workflow 的历史记录可以把长期稳定性明显较差的当前合格节点从 automatic stable tier 降级；历史记录**永远不会**把当前实时资格检测失败的节点重新提升回来。

历史数据保存在私有 Workers KV 中，仅保存 HMAC-SHA256 fingerprint 和聚合稳定性字段，不保存 runtime 节点名、server、凭据或订阅 URL。历史缺失、格式错误或暂时读取失败时，会退回纯当前运行的实时调度，不会扩大资格范围。

## 7. AI 调度规则

OpenAI、Claude、Gemini 会分别通过 loopback 临时 Mihomo 进行实际资格检测。同一个节点可能只通过其中一个服务。

三个服务拥有独立 qualified routing graph。如果某一个服务没有任何合格节点，只对该服务 fail closed，而不会借用未经该服务验证的节点；其他 AI 服务只要仍有合格节点就可以继续工作。

## 8. 查看 Production proof

Dry-run 或 publish 成功后，在 GitHub Actions Summary 查看最终 production proof。它只包含聚合信息，例如：

```text
candidate 字节数与 SHA-256
source reachability 状态
browsing tested / qualified / stable / reserve / rejected
各 AI 服务 qualified 数量
从 tools/mihomo-versions.json 读取的 stable Mihomo core 版本
publication = dry-run 或 published
```

可以用 SHA-256 标识“到底是哪一份精确 candidate 通过验证并发布”，而不暴露 candidate 内容。

## 9. 回滚生产配置

需要回滚时打开：

`Actions -> Roll back production config -> Run workflow`

设置：

```text
confirm = true
```

Rollback 只允许手动执行，并且只允许在 `main` 上执行。Workflow 优先读取私有 `previous-release-v1`，仅对 P17 之前的状态使用旧 `previous-v1` 兜底。激活前，旧 candidate 必须先通过**当前仓库版本**的 production/source-isolation 与 Routing V2 audit，再通过 `tools/mihomo-versions.json` 中全部 stable core，最后经相同 versioned release transaction 激活。

因此，一份旧配置即使 Mihomo 语法仍然有效，只要它已经不满足今天的来源权限，也不能被回滚上线。

不要把 rollback 当作解决“构建失败”的办法。新的 candidate 只要任一 gate 失败，本来就不会覆盖当前生产配置。

## 故障排查

### 订阅获取步骤失败

检查 `CLASH_RELAY_SUBSCRIPTIONS` 是否为有效 JSON，并包含 `subscriptions.yaml` 中所有启用来源要求的 Secret 名称。真实 URL 仍然只放在 Secret 界面。

### Browsing qualification 没有留下可用 provider

Workflow 会 fail closed 并保留原生产 KV。检查订阅可用性，以及 probe endpoint 是否能通过候选节点访问。不要为了强行发布而绕过 2/3 的资格边界。

### 某个 AI 服务 qualified 数量为 0

该服务会按设计 fail closed。OpenAI、Claude、Gemini 是独立判断，一个服务为 0 不代表另外两个服务也不可用。

### Mihomo 拒绝 candidate

公开 Workflow 故意不输出可能含凭据的详细 core 日志。应修复结构异常的订阅节点或项目配置，而不是跳过 stable core matrix。需要调整支持版本时，只修改 `tools/mihomo-versions.json` 及对应 checksum 声明。

### Cloudflare 发布失败

检查 `CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_KV_NAMESPACE_TITLE`，并确认 Token 有权读写目标 Workers KV namespace。激活前失败不会改变当前 production；若固定 key 更新后的 pointer commit 失败，则在存在旧值时执行补偿恢复。

### Scheduler history 或 AI cache 持久化失败

它们属于派生优化状态。生产发布已经成功时，状态写失败只显示 warning，不会使 production config 失效。下一次运行会用实时 qualification 重建缺失数据。

### Rollback 提示没有 previous release

只有“成功的新发布替换了一份不同的旧生产配置”后才会存在 previous release。首次发布、或 candidate bytes 与当前生产完全一致时，不会创建新的 previous release。

## 安全检查清单

正式使用 Fork 前确认：

- 真实订阅 URL 只存在于 GitHub Secrets。
- 从私密节点生成/资格处理后的 `config.yaml` 从不 commit，也不上传到 Artifact / Release / Gist。
- 除非你明确重新设计来源权限，否则 `subscription_1` 仍然只能进入 `browsing` 和 `ai`。
- 最终 `MATCH` 仍位于 general graph。
- 手动发布默认 `publish=false`。
- Rollback 必须明确 `confirm=true`，并重新通过 current-policy audit 与完整 pinned stable Mihomo matrix。
- Workflow 中 Mihomo 版本的唯一事实源是 `tools/mihomo-versions.json`。
- Cloudflare 凭据只拥有私有 KV 所需的最小权限。
