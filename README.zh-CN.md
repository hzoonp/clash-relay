# clash-relay

[English](README.md)

`clash-relay` 是一个确定性、fail-closed 的 Mihomo 配置生成项目，目标是在 **Public GitHub 仓库** 中安全生成真实生产配置。公开 YAML 只保存策略和订阅元数据；真实订阅 URL 只进入 GitHub Actions Secrets；包含节点凭据的最终 `config.yaml` 只在临时 GitHub Runner 上生成，并在通过两个 Mihomo 稳定版验证后直接写入私有 Cloudflare Workers KV。

最终配置是标准、单文件、standalone Mihomo YAML。FlClash 只需要受 `PROFILE_TOKEN` 保护的 Worker URL，不需要在运行时访问 GitHub 或 ACL4SSR。

> **敏感信息提示**：最终 `config.yaml` 内联真实节点凭据，应视为最高敏感数据。支持的 Public 生产工作流不会把它上传到 Actions Artifact、Release、Gist、Git commit 或 Pages。

## 当前生产模型

当前 canonical 生产配置已经精简为：

```text
4 个私密订阅 URL
  ├─ 订阅源 1
  ├─ 订阅源 2
  ├─ 订阅源 3
  └─ 订阅源 4
          ↓
     单一 general 节点库存
          ↓
        节点选择
          ↓
ACL4SSR Online Full（固定 commit）
          ↓
17 个中文可见策略组
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

生产配置共有 17 个中文可见组：

```text
节点选择
直连
广告拦截
谷歌FCM
微软服务
苹果服务
电报消息
人工智能
网易音乐
游戏平台
油管视频
奈飞视频
巴哈姆特
哔哩哔哩
国内媒体
国外媒体
漏网之鱼
```

这次精简删除了 4 个重复选择层：

- `Auto`：与唯一 `__CR_AUTO_GENERAL_ANY` 自动锚点重复；
- `App Purify`：合并到 `广告拦截`；
- `Microsoft Bing`：合并到 `微软服务`；
- `Microsoft OneDrive`：合并到 `微软服务`。

真正的节点凭据只由 `节点选择` 对应的 inline `proxy-provider` 持有。其它 ACL4SSR 组只是轻量策略选择器，不复制节点凭据。

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
  - GEOIP,CN,直连,no-resolve
  - MATCH,漏网之鱼
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
      获取 / 解析 / 去重
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

CI 在 Python 3.11/3.12 上运行单元测试与仓库审计，再做字节级确定性生成，并分别使用 Mihomo v1.19.30 / v1.19.29 做真实配置与启动集成验证。

详细说明：

- [配置模型](docs/configuration.md)
- [ACL4SSR 规则模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
