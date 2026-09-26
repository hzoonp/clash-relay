# clash-relay

[English](README.md)

`clash-relay` 2.x 是面向 Mihomo / FlClash 的确定性、fail-closed 配置生成项目：把多个私有订阅合并为一个独立 `config.yaml`，同时严格保留“订阅源 → 使用场景”的权限边界。

生成后的生产配置包含代理凭据，按最高敏感级别处理。生产环境只把经过完整验证的精确字节发布到私有 Cloudflare Workers KV；带凭据的配置不会进入 GitHub Artifact、Release、Gist、Pages 或 Git 历史。

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

自动 `push` 生产运行继续被硬锁为 dry-run；手动 `workflow_dispatch` 只有 `publish=true` 才发布。定时 `schedule` 只能通过专用 `CLASH_RELAY_SCHEDULE_PUBLISH` 门禁进入发布：已明确授权的上游 `hzoonp/clash-relay` 在该变量未设置时启用自动发布；公开 Fork 默认仍为 dry-run，只有显式把仓库变量设为精确小写 `true` 才启用。把变量设为 `false` 可立即暂停无人值守发布。最终字节完全不变时保持幂等，不旋转 previous-release 指针。

## 可选的 self-hosted 三网探测

独立的 **Self-hosted three-carrier probe** workflow 仅在 `main` 上手动触发、且仓库变量 `CLASH_RELAY_CARRIER_PROBE_ENABLED=true` 时运行。它先复用完整的 `ci.yml` 门禁并取得 `validated_sha`；三个运营商 job、collector 和历史提交 job 均检出该精确提交，在安装或运行仓库代码前确认 `GITHUB_SHA`、检出的 `HEAD` 与 `validated_sha` 一致。CI 门禁失败时，带 Secret 的 probe job 不会启动。分别在电信、联通、移动网络部署 Linux self-hosted runner，并配置专属标签 `carrier-probe-telecom`、`carrier-probe-unicom`、`carrier-probe-mobile`。为三个 job 配置限制分支的受保护 environment `carrier-probe-telecom`、`carrier-probe-unicom`、`carrier-probe-mobile`；另为 GitHub-hosted collector 和历史提交 job 配置限制分支的受保护 environment `carrier-probe-collector`。四个 environment 必须配置相同的 `CLASH_RELAY_SUBSCRIPTIONS` 输入与本仓库专用 Secret `CLASH_RELAY_CARRIER_HMAC_KEY`。workflow 调度器的 `runs-on` 标签和受保护 environment 才是 runner 与 Secret 的信任边界。应用层环境变量（包括调用者提供的标签字符串）不能独立证明 runner 身份。GitHub-hosted collector 只校验、摄入聚合证据，不运行 carrier producer。没有三网基础设施时，carrier evidence 保持 `not_configured`，正常生产发布独立运行。carrier workflow 用 `clash-relay-carrier-probe-${{ github.ref }}` 限制重复 campaign；正常发布使用独立的 `clash-relay-publish-${{ github.ref }}`。成功 preflight 之后，只有单独的历史提交 job 在写 history KV 时持有 `clash-relay-carrier-history-${{ github.ref }}` 锁；隐私安全的 receipt 通过编码的 job output 传递，不上传 artifact。三网 runner 排队及历史提交不会持有生产发布锁。

各 runner 在私有环境构建候选节点，并探测同一确定性、有数量上限的 endpoint 样本。TCP-native 节点执行 DNS 解析和有界 TCP 连接；UDP-native 节点计为 skipped。候选配置、目标、endpoint、凭据和原始样本留在私有 runner，仅匿名聚合 JSON 跨 job 传递。collector 校验三份聚合结果后，将 `carrier-qualification.json` 送入 canonical production preflight。preflight 重新生成 production candidate，用仅从环境变量读取的 HMAC key 重新计算匿名 sample、inventory、probe-plan ID；candidate 或 key 漂移时拒绝证据。仅当传入 carrier input 时才需要该 key。preflight 步骤即使持有 Cloudflare 凭据以读取 production state，依然严格零外部写入；完整成功后才会在 `.work/carrier-observation-receipt.json` 签发本地 privacy-safe observation receipt，绑定 schema 版本、aggregate digest、candidate/probe binding digest、collected epoch 与 validated SHA。随后由独立的显式 commit 步骤运行 `scripts/persist_carrier_observation.py` 并传入此 receipt，用受保护的仓库 HMAC key 验证 receipt MAC，并重新校验 digest 与 validated SHA，任何不一致都会 fail closed 且不写 history。普通 dry-run 不产生可提交 receipt。preflight 失败时 job 在 commit 步骤前终止，不会产生任何写入。workflow 在 commit 后清理本地 aggregate 和 receipt，不上传 probe artifact，也不发布配置。carrier evidence 仍为 `external_self_hosted_advisory`，客户端 URLTest 仍是本地最终判断。

采样用绑定本仓库的 HMAC 身份去重，并分别生成选中样本、完整 eligible inventory 和 probe plan 的匿名 ID。按规范地域、协议、订阅来源平衡，TCP 目标上限为 12；`ANY` 和 `CHAIN` 属于非地域 scope，同一 endpoint 的地域标签冲突时拒绝。collector 要求三网 sample、inventory、plan ID、采样数、sampler 版本一致，时间戳相差不超过五分钟。coverage（`partial`/`full`）与证据质量（`sufficient`/`insufficient`/`stale`）分开：sufficient 要求足量且全部完成的不同 TCP endpoint，并依据地域或来源多样性判断；小型 inventory 有明确的有界回退。protocol 标签只供观察，不单独提高证据质量。对外仅公开多样性计数和失败类别汇总（`dns_failure`、`connect_timeout`、`connection_refused`、`connect_failure`、`tcp_connected`），不公开 endpoint 身份。纯 UDP endpoint 计为 skipped；同时有 TCP occurrence 的 endpoint 不重复计入 skipped。证据始终仅供参考，不删节点，也不改变 Promotion Guard 或调度。

Carrier observation history 使用独立的 aggregate-only Cloudflare KV 状态（`<production-key>.carrier-observation-history-v1`，文档 schema v3），与 scheduler history、AI cache 完全隔离。只有通过校验 observation receipt 提交、candidate binding 通过的 campaign 才能进入该状态，因此 `binding_failed` 不是可持久化状态；可持久化状态为 `valid`、`stale`、`partial`、`insufficient` 与 `invalid`（仅限已绑定但聚合语义异常的安全情况）。只有 coverage 为 full、freshness 为 current、evidence 为 sufficient 的 campaign 才更新各运营商可达率、延迟和结果类别的 EMA。`recent_campaign_count` 统计最近 30 天窗口内的 campaign，并在每次写入与读取时回收过期条目。由签名 receipt 的 aggregate digest 派生的匿名 `campaign_id` 保证重试幂等；旧 campaign 不反向更新 EMA，同一秒的不同 campaign 按串行提交顺序分别记录。`status_counts_lifetime` 与每个运营商的 `campaign_runs_lifetime` 是上限 64 的饱和 lifetime 计数器，不是滚动 30 天值；运营商行仅在该运营商连续 30 天没有 bound valid campaign 后重置。延迟证据自带新鲜度字段：`latency_sample_runs` 统计有可达样本的 campaign 数，`last_latency_epoch` 记录最近一次测到延迟的 epoch，因此 reachable=0 的 campaign 既不会让旧延迟 EMA 看似本轮证据，也不会被伪造成 latency 0。状态仅输出数值计数和 EMA，不生成 `improving`、`stable`、`degrading` 判断。当前 aggregate-only 证据不能给单节点做三网排名；未来如需单节点评分，须另行设计保护隐私的 per-node evidence contract。

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

网页浏览采用地区内资格验证和历史稳定性调度，自动地区顺序为：

```text
US -> SG -> JP -> TW -> KR -> HK -> OTHER
```

手动地区选择绝不会静默跨国；自动模式只有优先地区整体不可用时才跨区。私有匿名 scheduler history 可以在当前 live-qualified 集合中降级不稳定节点，但不能扩大 source admission。

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
python scripts/audit_documentation_contract.py
python scripts/audit_architecture_contract.py
python scripts/audit_operational_slo_contract.py
python scripts/audit_service_qualification_contract.py
python scripts/audit_supply_chain.py
python scripts/audit_acl4ssr_fidelity.py
python scripts/repository_audit.py
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
- [2.0.0 release notes](docs/releases/2.0.0.md)
