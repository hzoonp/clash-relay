# clash-relay

[English](README.md)

`clash-relay` 是一个确定性、fail-closed 的 Mihomo 配置生成项目，目标是在 **Public GitHub 仓库** 中安全生成真实生产配置。公开 YAML 只保存策略和订阅元数据；真实订阅 URL 只进入 GitHub Actions Secrets；包含节点凭据的最终 `config.yaml` 只在临时 GitHub Runner 上生成，并在通过 AI 资格筛选和两个 Mihomo 稳定版验证后直接写入私有 Cloudflare Workers KV。

最终配置是标准、单文件、standalone Mihomo YAML。FlClash 只需要受 `PROFILE_TOKEN` 保护的 Worker URL，不需要在运行时访问 GitHub 或 ACL4SSR。

> **敏感信息提示**：最终 `config.yaml` 内联真实节点凭据，应视为最高敏感数据。支持的 Public 生产工作流不会把它上传到 Actions Artifact、Release、Gist、Git commit 或 Pages。

## 当前生产模型

当前 canonical 生产配置为：

```text
4 个私密订阅 URL
          ↓
订阅源级节点准入策略
          ↓
通用节点库存 + 按节点名称识别国家/地区
          ↓
AI 候选池：SG / JP / US / HK / TW / KR / OTHER
          ↓
可信 Runner 逐节点实际访问 ChatGPT / Claude / Gemini
          ↓
只保留三项都通过的 AI 节点
          ↓
ACL4SSR Online Full（固定 commit）
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

`services.yaml` 在生产中为空。`policies.yaml` 保留一个通用 `general` 节点池，并增加 7 个 AI 国家/地区候选池；它们仍属于同一个 `general` 模块，不恢复旧的 ChatGPT、Claude、Gemini 独立服务模块。旧的 Google Play、Bulk、Residential、EMBY、High Multiplier、Chain 生产声明仍保持移除。通用生成引擎的其它能力继续在 `tests/fixtures/project/` 中独立测试。

## AI 节点资格与国家分组

AI 节点先根据**节点名称**进行确定性国家/地区分类，目前识别：

```text
SG  新加坡
JP  日本
US  美国
HK  香港
TW  台湾
KR  韩国
OTHER 其它/无法可靠识别
```

这不是 GeoIP 探测，也不声称节点出口 IP 一定与名称一致；它只是根据常见中文/英文地区名、机场码、国旗和边界明确的地区缩写进行分类。无法可靠识别时进入 `OTHER`，不会猜测。

所有订阅都可以提供 AI **候选**节点，但候选资格本身不等于可用。可信 `main` Runner 会为候选节点启动临时 Mihomo，使用 Core API 把临时 selector 固定到具体节点，再让 Python 通过该 Mihomo 的本地 mixed-port 实际请求：

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

当前要求三项请求都返回配置允许的 HTTP 状态范围（生产为 `200-399`）。网络失败、超时、4xx/5xx 或任何一项不通过都会把该节点从 AI 池移除。普通 `节点选择` 不受 AI 资格筛选影响。

为了避免几百个节点完全串行测试，候选 provider 会被分片并以有上限的并发临时 Mihomo 进程执行；节点名称、服务器、凭据和单节点探测结果不会输出到公开 Actions 日志。最终只记录安全的聚合数量。

资格筛选完成后：

- 某国家/地区仍有合格节点：保留对应 `AI · <地区>` 组；
- 某国家/地区没有合格节点：从最终 `人工智能` 组移除；
- 所有地区都没有合格节点：生产发布 fail closed，不覆盖 Cloudflare KV 中上一版成功配置。

因此最终 FlClash 可见组数量会随实际资格结果变化。核心策略组始终包括：

```text
节点选择
人工智能
流媒体
国内服务
广告拦截
```

`人工智能` 下最多出现：

```text
AI · 新加坡
AI · 日本
AI · 美国
AI · 香港
AI · 台湾
AI · 韩国
AI · 其他地区
DIRECT
```

通用节点由 `节点选择` 的 inline provider 持有；AI 国家组使用独立的私密 inline provider，以便在发布前安全删除未通过资格测试的具体节点。最终 YAML 仍只存在于私密发布链路中。

## 订阅源级策略

每个订阅可以声明可选的 `max_node_multiplier`。过滤器只识别节点名称中明确写出的倍率，例如 `2x`、`x2.5`、`3倍`、`倍率:4`。当上限设为 `2.0` 时：

- 明确倍率 `<= 2.0`：保留；
- 明确倍率 `> 2.0`：在分类和 provider 生成前直接剔除；
- 名称没有明确倍率标记：保留，不猜测。

canonical 生产仍把 `subscription_1` 限制在明确的通用网页与 AI 路径：

- ACL4SSR `ProxyGFWlist` 可以使用 `subscription_1`；
- ACL4SSR `AI` / `OpenAi` 通过经过实时资格筛选的 AI 国家组可以使用 `subscription_1`；
- `流媒体`、`国内服务` 的普通代理路径排除 `subscription_1`；
- Telegram 排除 `subscription_1`；
- 未命中的最终 `MATCH` 流量排除 `subscription_1`。

这些普通路由限制不会复制额外的 general provider。生成器通过隐藏路由锚点和 Mihomo `exclude-filter` 过滤共享 provider 中带有对应订阅源前缀的运行时节点。如果受限路由没有其它允许节点，则 fail closed 到隐藏的 `REJECT`。

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
      临时 Mihomo：逐节点 AI 实际 HTTP(S) 资格筛选
             ↓
      删除不合格节点与空国家组
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

订阅 Secret 只出现在 mask 和 generate 步骤；AI 资格筛选读取的是已经生成的临时 candidate，不需要原始订阅 Secret；Cloudflare API Token 只出现在最后发布步骤。`PROFILE_TOKEN` 完全不进入 GitHub。

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

Cloudflare 模式的安全门禁会拒绝同时开启 Artifact、Release 或 Gist。任何生成、AI 资格筛选或 Mihomo 验证步骤失败，都不会覆盖 KV 中上一版成功配置。

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

CI 在 Python 3.11/3.12 上运行单元测试与仓库审计，再做字节级确定性生成，并分别使用 Mihomo v1.19.30 / v1.19.29 做真实配置、启动和 AI selector/mixed-port 状态验证。

详细说明：

- [配置模型](docs/configuration.md)
- [ACL4SSR 规则模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
