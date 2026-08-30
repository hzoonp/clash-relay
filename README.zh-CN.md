# clash-relay

[English](README.md)

`clash-relay` 是一个确定性、fail-closed 的 Mihomo 配置生成项目，目标是在 **Public GitHub 仓库** 中也能安全完成真实生产生成。公开 YAML 只保存策略和订阅元数据；真实订阅 URL 只进入 GitHub Actions Secrets；包含节点凭据的最终 `config.yaml` 只在临时 GitHub Runner 上生成并通过两个 Mihomo 稳定版验证，随后直接写入私有 Cloudflare Workers KV。

最终配置是标准 Mihomo YAML，FlClash 直接通过受 `PROFILE_TOKEN` 保护的 Worker URL 自动读取。

> **敏感信息提示**：最终 `config.yaml` 内联节点凭据，应视为最高敏感数据。支持的 Public 生产工作流不会把它上传到 Actions Artifact、Release、Gist、Git commit 或 Pages。

## 最终数据流

```text
Public GitHub
  ├─ config.yaml / subscriptions.yaml      公开元数据
  ├─ CLASH_RELAY_SUBSCRIPTIONS             GitHub Secret
  └─ CLOUDFLARE_API_TOKEN                  GitHub Secret
             ↓
      trusted main Actions
             ↓
      每个订阅 URL ::add-mask::
             ↓
      获取 / 解析 / 分类
             ↓
      生成私密 config.yaml
             ↓
      Mihomo v1.19.30
             ↓
      Mihomo v1.19.29
             ↓
      Cloudflare Workers KV
             ↓
      /profile/<PROFILE_TOKEN>
             ↓
            FlClash
```

订阅 Secret 只出现在 mask 和 generate 步骤；Cloudflare API Token 只出现在最后发布步骤；Mihomo 验证阶段拿不到这两类 Secret。`PROFILE_TOKEN` 完全不进入 GitHub。

## GitHub 需要的配置

### Secrets

```text
CLASH_RELAY_SUBSCRIPTIONS
CLOUDFLARE_API_TOKEN
```

### Variables

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

例如：

```text
CLOUDFLARE_KV_NAMESPACE_TITLE = clash-relay-config
```

`PROFILE_TOKEN` 不要放 GitHub，只保存在 Cloudflare Worker Secret 和 FlClash 的完整订阅 URL 中。

## 多订阅 Secret

`CLASH_RELAY_SUBSCRIPTIONS` 是一个 JSON/YAML 映射，因此可以放任意数量订阅：

```json
{
  "SUBSCRIPTION_1_URL": "<真实订阅 URL>",
  "SUBSCRIPTION_2_URL": "<真实订阅 URL>",
  "SUBSCRIPTION_3_URL": "<真实订阅 URL>",
  "SUBSCRIPTION_4_URL": "<真实订阅 URL>"
}
```

这里放的是原始服务商/机场订阅 URL，不是 Cloudflare Worker URL。

`subscriptions.yaml` 中的 `secret_name` 必须和这些键完全一致。

## Cloudflare

生产默认使用：

```yaml
publishing:
  artifact: false
  github_release:
    enabled: false
    allow_sensitive_public_release: false
  gist:
    enabled: false
    allow_sensitive_unlisted_gist: false
  cloudflare_kv:
    enabled: true
    key: production-config
```

Cloudflare 模式的安全门禁会拒绝同时开启 Artifact、Release 或 Gist。

Worker 负责读取 `production-config`，并只对正确的：

```text
https://<worker>.<workers-subdomain>.workers.dev/profile/<PROFILE_TOKEN>
```

返回 YAML。完整 URL 本身就是 Bearer Credential，谁拿到它，谁就能读取配置。

## 生产 Workflow

当 `main` 中存在 `config.yaml` 与 `subscriptions.yaml` 后，**Generate, validate, and publish** 会：

1. 先验证 Cloudflare-only 发布策略；
2. 在任何订阅抓取之前，对每个实际 URL 执行 `::add-mask::`；
3. 在单个临时 Runner 上生成 `.work/private/config.yaml`；
4. 使用 Mihomo v1.19.30 验证同一个 candidate；
5. 使用 Mihomo v1.19.29 再验证同一个 candidate；
6. 两个版本都通过后，最后一步才获得 `CLOUDFLARE_API_TOKEN`；
7. 自动按 `CLOUDFLARE_KV_NAMESPACE_TITLE` 精确查找 Namespace；
8. 把验证过的原始字节写入 `production-config`；
9. 成功后删除 Runner 上的私密 candidate。

任何前置步骤失败，都不会修改 Cloudflare 中之前的成功配置。

真实 Mihomo 验证如果失败，详细 stdout/stderr 只保存在临时 Runner 文件中，不打印到 Public Actions 日志，也不会上传 Artifact。

## Public 仓库安全要求

- 保护 `main`，要求 PR CI 通过；
- 限制可修改 Workflow 和生产 Python 代码的人员；
- 不使用 `pull_request_target` 运行不可信代码并读取生产 Secrets；
- `config.yaml` / `subscriptions.yaml` 只能包含公开策略和元数据；
- 真实 URL、节点 UUID/password、Cloudflare API Token、完整 FlClash Worker URL 都不得提交；
- `PROFILE_TOKEN` 泄露时立即轮换。

## 节点能力

节点来源和 capability 相互独立。支持：

- `general`
- `ai`
- `bulk`
- `residential`
- `emby`
- `high_multiplier`
- `chain`

restricted capability 必须显式启用。空的可选业务池进入 `REJECT`，必需池为空则构建失败，不会跨业务借线。

详细说明：

- [配置模型](docs/configuration.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)

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

PR CI 使用完全虚构的 fixture，并分别在 Python 3.11/3.12、Mihomo v1.19.30/v1.19.29 上验证。
