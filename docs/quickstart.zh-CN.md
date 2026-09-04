# Fork 配置快速上手

这是从全新 Fork 到私有生产配置的最短受支持路径。正常首次部署只需要配置仓库 Secret / Variable，再完成一次 dry-run；不需要把任何真实订阅凭据写进受版本控制的文件。

## 10 分钟检查清单

```text
1. Fork 仓库
2. 配置 CLASH_RELAY_SUBSCRIPTIONS
3. 配置 Cloudflare token + account/namespace variables
4. 运行 clash-relay doctor --public-only
5. 使用私有输入运行 clash-relay doctor
6. 手动运行 Generate, validate, and publish，publish=false
7. 查看 aggregate proof；确认通过后再 publish=true
```

首次手动 Workflow 默认应保持 `publish=false`。把成功的 dry-run 作为第一次正式发布的前置条件。

## 1. Fork 时不要加入任何凭据

`config.yaml`、`subscriptions.yaml`、`policies.yaml`、policy fragments、schema、rules、源代码和 Workflow 可以公开；真实订阅 URL 与生成后的生产 `config.yaml` 字节不能提交进仓库。私有凭据和生成后的生产配置从不 commit，也不会上传到 GitHub Artifact / Release / Gist。

Canonical production 只接受 Policy Model v2：`policies.yaml` 只作为 manifest，routing、scheduling、classification、topology 分别由独立 fragment 持有。旧的单文件 Policy Model v1 不再是运行时输入；如需迁移，先离线运行 `scripts/migrate_policy_v2.py` 转换后再使用 clash-relay。

## 2. 添加订阅 Secret

创建仓库 Secret：

```text
CLASH_RELAY_SUBSCRIPTIONS
```

内容使用 JSON 或 YAML mapping，key 必须与 `subscriptions.yaml` 中已启用条目的 `secret_name` 一致。

示例：

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

以上 URL 仅为占位示例。`clash-relay doctor --public-only` 会显示需要配置的 Secret 名称，但绝不会显示 Secret 值。

## 3. 配置私有 Cloudflare KV

配置：

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

Token 只授予 Workflow 所需的最小 Workers KV 权限。Namespace title 必须唯一解析到一个 namespace。

## 4. 正式运行前先执行 doctor

只检查公共声明：

```bash
clash-relay doctor --public-only
```

公共报告会明确给出：

- 已启用订阅数量和对应 Secret 名称；
- Policy Model v2 readiness；
- pinned stable Mihomo core 数量；
- scheduler policy 是否声明；
- 下一步首次 dry-run 操作建议。

检查本地私有 Secret readiness：

```bash
clash-relay doctor --secret-file .secrets.yaml
```

如需使用与生产一致的有界 fetch 策略实际检查全部已启用订阅的连通性，但不输出 URL 或订阅内容：

```bash
clash-relay doctor --secret-file .secrets.yaml --check-subscriptions
```

如需验证 Cloudflare account/token/namespace/key 的只读连通性，但不发布任何配置：

```bash
clash-relay doctor --secret-file .secrets.yaml --check-cloudflare
```

两个连通性检查可以同时启用。Doctor 输出只包含聚合状态，不会打印订阅 URL、订阅内容、Cloudflare 凭据或 production config 字节；失败信息也只保留安全的公开标识和状态。

## 5. 先做 dry-run

手动运行 `Generate, validate, and publish`，把 Workflow input 设为 `publish = false`。

成功的 dry-run 会执行与生产相同的私有生成、source audit、browsing/transport qualification、AI qualification、qualification 后审计，以及 `tools/mihomo-versions.json` 中全部 stable Mihomo core 验证，但不会激活 Cloudflare KV 生产字节。

P39 的 retry 非常严格：只有结构化、整轮 browsing live probe 全部失败、且原因仅属于 probe/timeout/HTTP 429/5xx 一类基础设施瞬时问题时，才允许从 immutable generated candidate 完整重试一次。部分节点成功、policy/inventory rejection、UDP/TCP admission failure、Mihomo core failure、配置错误和 protocol error 都不会重试。

只查看 GitHub Actions 中的 aggregate production proof。

## 6. 正式发布

手动运行 `publish = true`，或者在仓库明确配置为 main push 发布时合并经过验证的变更。

发布流程先写入并 read-back 验证不可变 release 对象，再激活固定客户端 production key，最后提交 release pointers。Cloudflare KV 不支持跨 key 事务，因此 pointer commit 异常时使用补偿恢复上一版本的精确生产字节。

Release progress 明确为：

```text
prepared -> qualified -> promoted -> published -> verified
```

如果 client-visible release transaction 已经成功提交，而后续 proof / manifest / metrics 出现异常，系统会把它报告为 post-release observability 降级，而不会错误宣称“发布没有发生”。发布前所有 mandatory gate 仍保持 fail-closed。

首次成功发布后，P26 会每 6 小时自动运行同一条生产 Workflow（UTC `17 */6 * * *`）。定时刷新不是简化路径：订阅获取、生成、source isolation、browsing/transport 与 AI qualification、OpenAI client-path hardening、qualification 后审计、Promotion Guard，以及全部 stable Mihomo 验证都必须通过后才能发布。如果最终字节没有变化，则 release 保持 `status: unchanged`，并且不会旋转 `previous-release-v1`。

多个 publish-triggering Actions 同时到达时会通过 concurrency group 串行执行，且 `cancel-in-progress: false`；新运行不会在旧 release transaction 中途取消旧任务。

## 7. 配置 FlClash / Mihomo

客户端继续使用固定 production key 对应的私有订阅入口。内部 release SHA 改变时，客户端 URL 不需要变化；P26 定时刷新也不要求在 FlClash 中更换订阅 URL。

顶层只显示：

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

## 8. 安全回滚

只有明确需要回滚时，才运行手动 `Roll back production config` Workflow，并设置 `confirm = true`。Workflow 优先解析 `previous-release-v1`；legacy slot 只用于迁移兼容。历史 candidate 必须重新通过 current-policy source/routing audit 和 `tools/mihomo-versions.json` 中全部当前 stable core 验证，再通过相同 versioned release transaction 激活。

违反当前 source isolation 的历史配置不会被允许回滚。测试环境还会演练 exact previous-release round trip 和 current/previous pointer 反转，但不会触碰真实生产数据。

## 始终保持私有的数据

以下内容不能发布到 GitHub：

- 真实订阅 URL；
- 生成后的代理凭据；
- qualification 后私有 candidate；
- 节点级 browsing / AI probe 结果；
- scheduler fingerprint key；
- AI cache fingerprint。

私有 production metrics 保留最多 30 次、且只保存 aggregate-only 信息，可以包含安全计数、hash、stage duration、retry recovery 次数、Promotion Guard 状态和 release phase，但不能包含节点身份、server、凭据或订阅 URL。

## 兼容性安全契约

既有 browsing scheduler 契约保持不变：实时探测 **3/3** 成功进入 Stable，**2/3** 成功进入 Reserve。私有 scheduler history 继续使用 **HMAC-SHA256** fingerprint，不能把当前 live-failed 节点提升回 Stable。OpenAI、Claude、Gemini 继续独立 qualification。手动恢复仍通过 **Roll back production config**，必须设置 **confirm = true**，验证 **tools/mihomo-versions.json** 中全部 stable core，解析 **previous-release-v1**，并在激活前执行 **current-policy** audit。
