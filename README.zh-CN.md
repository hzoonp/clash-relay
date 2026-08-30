# clash-relay

[English](README.md)

`clash-relay` 是一个确定性、fail-closed 的 Mihomo 配置生成项目，目标是在 **Public GitHub 仓库** 中安全生成真实生产配置。公开 YAML 只保存策略和订阅元数据；真实订阅 URL 只进入 GitHub Actions Secrets；包含节点凭据的最终 `config.yaml` 只在临时 GitHub Runner 上生成，并在通过两个 Mihomo 稳定版验证后直接写入私有 Cloudflare Workers KV。

最终配置是标准、单文件、standalone Mihomo YAML。FlClash 只需要受 `PROFILE_TOKEN` 保护的 Worker URL，不需要在运行时访问 GitHub 或 ACL4SSR。

> **敏感信息提示**：最终 `config.yaml` 内联真实节点凭据，应视为最高敏感数据。支持的 Public 生产工作流不会把它上传到 Actions Artifact、Release、Gist、Git commit 或 Pages。

## 当前生产模型

当前 canonical 生产配置已经精简为：

```text
4 个私密订阅 URL
          ↓
订阅源级节点准入策略
          ↓
单一 general 节点库存
          ↓
        节点选择
          ↓
ACL4SSR Online Full（固定 commit）
          ↓
5 个 FlClash 可见策略组
          ↓
Mihomo v1.19.30 + v1.19.29
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

生产只启用：

```yaml
modules:
  general: true
```

`services.yaml` 在生产中为空，`policies.yaml` 只保留一个 `general` 节点池。旧的 ChatGPT、Claude、Gemini、Google Play、Bulk、Residential、EMBY、High Multiplier、Chain 生产声明已经移除。通用生成引擎仍保留这些数据驱动能力，并在 `tests/fixtures/project/` 中独立测试，避免测试夹具污染真实生产配置。

## 当前 FlClash 可见策略组

生产配置只保留 5 个可见组：

```text
节点选择
人工智能
流媒体
国内服务
广告拦截
```

真正的节点凭据只由 `节点选择` 对应的 inline `proxy-provider` 持有。其它 4 个组只是轻量策略选择器，不复制节点凭据。无需人工切换的直连规则直接使用 Mihomo `DIRECT`，最终兜底也不再为了 UI 单独制造一个“漏网之鱼”组。

## 订阅源级策略

每个订阅可以声明可选的 `max_node_multiplier`。过滤器只识别节点名称中明确写出的倍率，例如 `2x`、`x2.5`、`3倍`、`倍率:4`。当上限设为 `2.0` 时：

- 明确倍率 `<= 2.0`：保留；
- 明确倍率 `> 2.0`：在分类和 provider 生成前直接剔除；
- 名称没有明确倍率标记：保留，不猜测。

canonical 生产进一步把 `subscription_1` 限制在明确的通用网页与 AI 路径：

- ACL4SSR `ProxyGFWlist` 可以使用 `subscription_1`；
- ACL4SSR `AI` / `OpenAi` 通过 `人工智能` 可以使用 `subscription_1`；
- `流媒体`、`国内服务` 的代理路径排除 `subscription_1`；
- Telegram 排除 `subscription_1`；
- 未命中的最终 `MATCH` 流量排除 `subscription_1`。

这些限制不会复制节点。生成器只克隆隐藏的路由锚点，并通过 Mihomo `exclude-filter` 过滤共享 provider 中带有对应订阅源前缀的运行时节点。如果受限路由已经没有其它允许节点，则 fail closed 到隐藏的 `REJECT`，不会偷偷回退到被禁止的订阅源。

这里约束的是**规则路由场景**，不是进程识别；项目不会声称能够仅凭域名证明发起流量的可执行程序一定是浏览器。

## ACL4SSR 规则模型

ACL4SSR 固定到不可变 commit，而不是跟随移动的 `master`。构建时从该 commit 获取 Full 规则片段，并转换为：

```yaml
rule-providers:
  acl4ssr_ai:
    type: inline
    behavior: classical
    payload: [...]

rules:
  - RULE-SET,acl4ssr_ai,人工智能
  - GEOIP,CN,DIRECT,no-resolve
  - MATCH,<source-filtered-final-anchor>
```

因此：

- ACL4SSR 数据在可信构建阶段获取；
- 最终 YAML 已包含完整所需规则；
- FlClash/Mihomo 运行时不依赖 GitHub；
- rule-provider 不含 `url` 或 `path`；
- 每次规则拓扑变化都必须通过两版真实 Mihomo 验证。

详见 [ACL4SSR 规则模型](docs/rules.md)。

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
      获取 / 解析 / 准入过滤 / 去重
             ↓
      获取固定 ACL4SSR 规则
             ↓
      生成私密 standalone YAML
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

`CLASH_RELAY_SUBSCRIPTIONS` 是一个 JSON/YAML 映射：

```json
{
  "SUBSCRIPTION_1_URL": "<真实订阅 URL>",
  "SUBSCRIPTION_2_URL": "<真实订阅 URL>",
  "SUBSCRIPTION_3_URL": "<真实订阅 URL>",
  "SUBSCRIPTION_4_URL": "<真实订阅 URL>"
}
```

这里保存的是原始服务商订阅 URL，不是 Cloudflare Worker URL。`subscriptions.yaml` 中的 `secret_name` 必须与这些键完全一致。`PROFILE_TOKEN` 不要放 GitHub。

## Cloudflare-only 发布

生产保持：

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

Cloudflare 模式的安全门禁会拒绝同时开启 Artifact、Release 或 Gist。任何生成或 Mihomo 验证步骤失败，都不会覆盖 KV 中上一版成功配置。

## Public 仓库安全要求

- 保护 `main`，要求 PR CI 通过；
- 限制可修改 Workflow 和生产 Python 代码的人员；
- 不使用 `pull_request_target` 运行不可信代码并读取生产 Secrets；
- 公开 YAML 不得包含真实 URL、token、用户名、密码或节点凭据；
- 完整 Worker URL 本身是 Bearer Credential；
- `PROFILE_TOKEN` 泄露时立即轮换。

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

通用引擎的 fictional fixture 与真实生产声明完全隔离。生成 fixture 时使用：

```bash
python scripts/make_fixture_sources.py
clash-relay generate \
  --config tests/fixtures/project/config.yaml \
  --subscriptions tests/fixtures/project/subscriptions.yaml \
  --services tests/fixtures/project/services.yaml \
  --policies tests/fixtures/project/policies.yaml \
  --secret-file .work/fixture-secrets.yaml \
  --output .work/config.yaml
```

CI 在 Python 3.11/3.12 上运行单元测试与仓库审计，再做字节级确定性生成，并分别使用 Mihomo v1.19.30 / v1.19.29 做真实配置与启动集成验证，包括 source-filtered `exclude-filter` 路由。

详细说明：

- [配置模型](docs/configuration.md)
- [ACL4SSR 规则模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
