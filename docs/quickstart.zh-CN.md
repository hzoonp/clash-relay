# Fork 配置快速上手

这是从全新 Fork 到私有生产配置的最短受支持路径。

## 1. Fork 时不要加入任何凭据

`config.yaml`、`subscriptions.yaml`、`services.yaml`、`policies.yaml`、schema、rules、源代码和 Workflow 可以公开；真实订阅 URL 与生成后的生产 `config.yaml` 字节不能提交进仓库。私有凭据和生成后的生产配置从不 commit，也不会上传到 GitHub Artifact / Release / Gist。

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

以上 URL 仅为占位示例。

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

网页 qualification 保留既有采样语义：`3/3` 为 Stable，`2/3` 为 Reserve。Scheduler history 继续通过 HMAC-SHA256 指纹保存私有匿名历史；OpenAI、Claude、Gemini 分别独立 qualification，并按服务 fail-closed。

只查看 GitHub Actions 中的 aggregate production proof。

## 6. 正式发布

手动运行 `publish = true`，或者在仓库明确配置为 main push 发布时合并经过验证的变更。

发布流程先写入并 read-back 验证不可变 release 对象，再激活固定客户端 production key，最后提交 release pointers。Cloudflare KV 不支持跨 key 事务，因此 pointer commit 异常时使用补偿恢复上一版本的精确生产字节。

首次成功发布后，P26 会每 6 小时自动运行同一条生产 Workflow（UTC `17 */6 * * *`）。定时刷新不是简化路径：订阅获取、生成、source isolation、browsing/transport 与 AI qualification、OpenAI client-path hardening、qualification 后审计，以及全部 stable Mihomo 验证都必须通过后才能发布。如果最终字节没有变化，则 release 保持 `status: unchanged`，并且不会旋转 `previous-release-v1`。

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

违反当前 source isolation 的历史配置不会被允许回滚。

## 始终保持私有的数据

以下内容不能发布到 GitHub：

- 真实订阅 URL；
- 生成后的代理凭据；
- qualification 后私有 candidate；
- 节点级 browsing / AI probe 结果；
- scheduler fingerprint key；
- AI cache fingerprint。

私有 production metrics 也只允许保存有界、聚合后的状态。
