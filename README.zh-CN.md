# clash-relay

[English](README.md)

`clash-relay` 2.x 是面向 Mihomo / FlClash 的确定性、fail-closed 配置生成项目：把多个私有订阅合并为一个独立 `config.yaml`，同时严格保留“订阅源 → 使用场景”的权限边界。

生成后的生产配置包含代理凭据，按最高敏感级别处理。生产环境只把经过完整验证的精确字节发布到私有 Cloudflare Workers KV；带凭据的配置不会进入 GitHub Artifact、Release、Pages 或 Git 历史。

权威行为契约集中在 [Publishing](docs/publishing.md)、[Routing V2](docs/routing-v2.md) 和 [Rules/source policy](docs/rules.md)；其他页面只作为使用指南，不再重复承担策略权威。

## 从 Fork 开始

新 Fork 使用 [Fork 快速上手](docs/quickstart.zh-CN.md)：

```text
Fork
  -> 配置 CLASH_RELAY_SUBSCRIPTIONS
  -> 配置 Cloudflare KV
  -> clash-relay doctor
  -> 手动 dry-run（publish=false）
  -> 查看聚合 production proof
  -> 手动 bootstrap publish=true
  -> 显式启用受保护的每 6 小时定时发布
  -> 必要时执行 validated 回滚
```

`clash-relay doctor` 会检查公共声明、订阅 Secret 是否齐全、Mihomo 版本清单，以及可选的 Cloudflare 只读连通性；它不会发布生产配置。

`clash-relay effective-config` 展示网络 profile 生效后的公共 DNS 与 URLTest 声明及被覆盖字段，不读取订阅 Secret。

`clash-relay audit-release-state` 只读检查私有 Cloudflare 发布状态；`clash-relay doctor --check-release-state` 将该检查纳入私有就绪诊断。

每个 main push 只完整执行一次代码验证矩阵，不进入 production lifecycle，也不读取生产订阅 Secrets。之后的手动或定时刷新只复用“同一个 main SHA 已成功”的代码验证结果，同时仍重新获取订阅并完整执行候选资格筛选、策略审计、Promotion Guard 与 Mihomo 验证。手动 `workflow_dispatch` 只有 `publish=true` 才发布；定时发布仍由 `CLASH_RELAY_SCHEDULE_PUBLISH` 控制。

## Public Config v2

受支持的跟踪配置面保持最小化：

```text
config.yaml          version: 2
subscriptions.yaml   version: 2
policies.yaml        version: 2 manifest
policies/*           Policy Model v2 独立职责 fragments
```

删除的 v1 公共字段不提供 runtime alias。Policy Model v1 不是运行时输入；`scripts/migrate_policy_v2.py` 只是一项离线迁移工具。

FlClash 顶层只暴露六个主要场景：

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

ACL4SSR 兼容组、地区辅助组、自动调度组和 qualification 运行时组保持隐藏。

## 订阅源权限

合并订阅不代表所有订阅都能进入所有场景：

```text
SUBSCRIPTION_1_URL
  ├─ 明确 >2x          -> 剔除
  ├─ 恰好 2x           -> 保留
  ├─ EMBY 标记         -> 剔除
  ├─ browsing           -> 允许
  ├─ ai                 -> 允许
  └─ general/media/...  -> 禁止

SUBSCRIPTION_2_URL .. SUBSCRIPTION_5_URL
  ├─ general            -> 允许
  ├─ browsing           -> 允许
  └─ ai                 -> 禁止
```

`ingest_order` 只控制确定性的订阅摄入/去重顺序，不表示路由优先级或节点质量。

生产不变量：

1. `subscription_1` 只能进入 browsing 和 AI inventory。
2. subscription-1 中 EMBY 标记节点在生成 inventory 前按大小写不敏感方式剔除。
3. 明确倍率严格大于 `2x` 的节点在分类、去重前剔除；恰好 `2x` 和未标倍率节点保留。
4. 流媒体、消息通讯、下载、ACL 兼容选择器以及最终 `MATCH` 都不能到达 `subscription_1`。
5. qualification 前后都会执行 source reachability audit。

## Compiler 与 RuntimeGraph

v2 的生产数据流为：

```text
Declarations
  -> Subscription I/O
  -> NodeInventory
  -> PolicyCompiler
  -> RuntimeGraph
  -> qualification
  -> Qualified Graph
  -> MihomoSerializer
  -> config.yaml
  -> audit / real Mihomo / promotion
```

Builder 在 compiler 输出后不再修改 topology。Python 内部阶段通过 typed in-process application API 直接调用；只有 Mihomo 这类真实外部程序保留 subprocess 边界。

## ACL4SSR 一致性

`rules/acl4ssr.yaml` 固定 ACL4SSR Online 参考版本。ACL4SSR 负责基线分类语义；clash-relay 负责 source-safe inventory、声明式扩展、qualification 和调度。

明确且受审计的偏差：

- `BanProgramAD / 应用净化` 保持禁用，避免已确认的移动端图片/CDN 破坏。
- AI/OpenAI 在宽泛 `ProxyMedia` 之前处理。
- `Download.list` 在 `ProxyLite` 之前处理并指向 `下载流量`。
- ACL4SSR 单订阅裸节点通配逻辑改造成 source-aware 场景选择器。

## Qualification 与调度

网页浏览保留地区内资格验证和历史稳定性调度。`网页自动` 使用跨地区 `url-test`，由 FlClash 所在真实网络测速选区，测速间隔为 300 秒，切换容差为 150 毫秒。每个地区仍保留 Stable → Reserve 恢复路径，手动地区选择保持固定地区。私有匿名 scheduler history 可以在当前 live-qualified 集合中降级不稳定节点，但不能扩大 source admission。

AI 服务通过通用 `ServiceQualification` registry 进行资格验证。OpenAI、Claude、Gemini 都只是注册实现，主 qualification pipeline 不包含厂商分支。服务特有的 critical/supporting probes、cache TTL、route post-processing 和可选 client-path hardening 都封装在对应实现中。

OpenAI 继续保留经过审查的 ChatGPT App contract 和 route lock；client-path hardening 改为 Policy 声明后执行，并且只能发生在服务器端资格通过之后。TLS 证书和 hostname 校验始终开启。

## 生产发布模型

生产只有一条私有发布路径：

```text
generated graph
  -> browsing + transport qualification
  -> ServiceQualification registry
  -> 声明式 service client-path hardening
  -> qualification 后策略审计
  -> Promotion Guard
  -> tools/mihomo-versions.json 中全部 stable core
  -> versioned Cloudflare KV release transaction
  -> 固定客户端 production key
```

`tools/mihomo-versions.json` 是 stable/prerelease Mihomo 版本的唯一事实来源。Workflow 和文档不得维护第二套固定 stable 版本列表。

每个 source/production release 都绑定到一个精确 validated commit SHA。质量门禁覆盖 Python 3.11/3.12/3.13、hash-verified 依赖、Ruff、application boundary 静态类型、测试与 coverage、架构/供应链/隐私审计、deterministic generation、Routing V2 drift，以及真实 Mihomo startup/provider 集成。

私有生产 candidate 按精确字节 SHA-256 存成不可变 release 对象：

```text
<production>.release-v1.<sha256>.config
<production>.release-v1.<sha256>.manifest
<production>.current-release-v1
<production>.previous-release-v1
<production>.release-journal-v1
```

这里的 `v1` 表示稳定的**私有存储 schema 版本**，不是 clash-relay 产品大版本。v2 删除旧 `previous-v1` rollback slot/fallback。回滚必须通过 versioned previous pointer 解析，验证精确 SHA-256 字节和 immutable manifest，再通过当前策略审计和完整 stable Mihomo matrix 后才能激活。

Cloudflare KV 不是跨 key 事务数据库，因此 versioned Cloudflare KV release transaction 使用补偿语义：先 stage 并 read-back 验证不可变字节，再激活固定 production key，随后提交 pointers；如果 commit 失败，尝试恢复上一版本的精确生产字节。

## Operational SLO 与隐私

Production proof、production metrics 和 operational SLO history 只保存聚合运维信息。SLO ring 可以统计 qualification rejection rate、retry recovery rate、Promotion Guard block rate、lifecycle duration 和 candidate churn，但不保存节点身份或订阅数据。SLO 持久化是 best-effort，绝不会放宽生产门禁。

公共或持久化聚合数据都不会包含节点名、服务器、端口、凭据、订阅 URL、生成配置字节或子进程详细诊断。

## GitHub Secrets / Variables

推荐订阅 Secret：

```text
CLASH_RELAY_SUBSCRIPTIONS
```

示例结构：

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4",
  "SUBSCRIPTION_5_URL": "https://example.invalid/subscription-5"
}
```

Cloudflare 发布还需要：

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

定时发布控制变量：

```text
Variable: CLASH_RELAY_SCHEDULE_PUBLISH
```

公开 Fork 必须先完成成功的手动 dry-run 和 bootstrap 发布，再把它显式设为精确小写 `true`；设为 `false` 会让定时路径保持或恢复为 dry-run。已授权的上游仓库在变量未设置时默认启用，也可以通过设为 `false` 暂停。

真实订阅 URL 绝不能写入受跟踪 YAML、README、Workflow 参数或日志。

## 本地开发

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-build-isolation --no-deps -e .
clash-relay doctor --public-only
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/run_project_checks.py typecheck
python scripts/run_project_checks.py audit --offline-acl4ssr
```

## 文档

- [Fork 快速上手](docs/quickstart.zh-CN.md)
- [Fork quickstart](docs/quickstart.md)
- [架构](docs/architecture.md)
- [配置模型](docs/configuration.md)
- [Service Qualification API](docs/service-qualification.md)
- [Operational SLO](docs/operational-slo.md)
- [Production maturity](docs/production-maturity.md)
- [OpenAI App reliability](docs/openai-app-reliability.md)
- [ACL4SSR 路由模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布](docs/publishing.md)
- [版本与兼容性](docs/versioning.md)
- [v2 发布检查清单](docs/release-checklist.md)
- [2.3.0 release notes](docs/releases/2.3.0.md)
