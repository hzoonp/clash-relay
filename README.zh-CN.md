# clash-relay

[English](README.md)

`clash-relay` 是一个面向 Mihomo / FlClash 的确定性配置生成项目：把多个私有订阅合并为一个独立 `config.yaml`，同时严格保留“订阅源 → 使用场景”的权限边界。

生成后的生产配置包含代理凭据，按最高敏感级别处理。生产环境只把经过完整验证的精确字节发布到私有 Cloudflare Workers KV；带凭据的配置不会进入 GitHub Artifact、Release、Gist、Pages 或 Git 历史。

## 从 Fork 开始

新 Fork 推荐流程：

```text
Fork
  -> 配置 CLASH_RELAY_SUBSCRIPTIONS
  -> 配置 Cloudflare KV
  -> clash-relay doctor
  -> 手动 dry-run（publish=false）
  -> 查看聚合 production proof
  -> publish=true
  -> 必要时执行 release-aware rollback
```

`clash-relay doctor` 会检查公共声明、订阅 Secret 是否齐全、Mihomo 版本清单，以及可选的 Cloudflare 只读连通性；它不会发布生产配置。

## 六个公开场景

FlClash 顶层只暴露：

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

合并订阅并不代表所有订阅都能进入所有场景：

```text
SUBSCRIPTION_1_URL
  ├─ 明确 >2x          -> 剔除
  ├─ EMBY 标记         -> 剔除
  ├─ 网页浏览           -> 允许
  ├─ 人工智能           -> 允许
  └─ general/media/...  -> 禁止

SUBSCRIPTION_2+
  ├─ general
  ├─ browsing
  └─ ai
```

生产不变量：

1. `subscription_1` 只能进入 browsing 和 AI inventory。
2. subscription-1 中 EMBY 标记节点在生成 inventory 前按大小写不敏感方式剔除。
3. 明确倍率严格大于 `2x` 的节点在分类、去重前剔除；恰好 `2x` 和未标倍率节点保留。
4. 流媒体、消息通讯、下载、ACL 兼容选择器以及最终 `MATCH` 都不能到达 `subscription_1`。
5. qualification 前后都会执行 source reachability audit。

## ACL4SSR 一致性

`rules/acl4ssr.yaml` 固定 ACL4SSR Online 参考版本。ACL4SSR Online 负责分类语义；clash-relay 负责 source-safe inventory、qualification 与调度。

明确且受审计的偏差：

- `BanProgramAD / 应用净化` 保持禁用，避免已确认的移动端图片/CDN 破坏。
- AI/OpenAI 在宽泛的 `ProxyMedia` 之前处理。
- `Download.list` 在 `ProxyLite` 之前处理并指向 `下载流量`。
- ACL4SSR 单订阅的裸节点通配逻辑改造成 source-aware 场景选择器。

## Qualification 与调度

网页浏览采用地区内资格验证和历史稳定性调度，自动地区顺序为：

```text
US -> SG -> JP -> TW -> KR -> HK -> OTHER
```

手动选择地区绝不会静默跨国；自动模式只有当前优先地区整体不可用时才跨区。调度历史是私有匿名状态，只能在当前 live-qualified 集合中降级不稳定节点，不能扩大 source admission。

AI 对 OpenAI、Claude、Gemini 分别独立验证。香港在 AI qualification 前排除，每个服务独立 fail-closed。

## 生产发布模型

生产只有一条统一的私有 qualification 流程：

```text
generated.yaml
  -> browsing + transport qualification
  -> AI qualification
  -> qualification 后策略审计
  -> tools/mihomo-versions.json 中全部 stable core
  -> versioned Cloudflare KV release transaction
  -> 固定客户端生产 key
```

`tools/mihomo-versions.json` 是 stable/prerelease Mihomo 版本的唯一事实来源。Workflow 和文档不得再维护第二套固定 stable 版本列表。

每个生产候选都按精确字节 SHA-256 写入不可变 release 对象：

```text
<production>.release-v1.<sha256>.config
<production>.release-v1.<sha256>.manifest
<production>.current-release-v1
<production>.previous-release-v1
```

Cloudflare KV 不提供跨 key 数据库事务，因此这里采用补偿事务：先 stage 并 read-back 验证不可变字节，再激活固定 production key，随后提交 release pointers；如果 pointer commit 失败，则尝试恢复上一版本的精确生产字节。

Rollback 会读取 previous release，并使用**当前仓库策略**和当前 `tools/mihomo-versions.json` 中全部 stable core 重新验证后再激活。

## 可观测性与隐私

Production proof 和私有纵向 metrics 只保存聚合运维信息，例如 candidate SHA/大小、合格节点数量、地区 cohort 计数、AI 服务计数、release 状态、Mihomo 验证数量和有界阶段耗时。不会保存节点名、服务器、凭据、订阅 URL 或子进程详细诊断。

P18.1-P23 的长期生产契约见 [Production maturity](docs/production-maturity.md)。

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
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

Cloudflare 发布还需要：

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

真实订阅 URL 绝不能写入受跟踪 YAML、README、Workflow 参数或日志。

## 本地开发

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
clash-relay doctor --public-only
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/audit_documentation_contract.py
python scripts/audit_acl4ssr_fidelity.py
python scripts/repository_audit.py
```

## 文档

- [配置快速上手](docs/quickstart.zh-CN.md)
- [Fork quickstart](docs/quickstart.md)
- [Production maturity](docs/production-maturity.md)
- [配置模型](docs/configuration.md)
- [架构](docs/architecture.md)
- [ACL4SSR 路由模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布](docs/publishing.md)
- [版本与兼容性](docs/versioning.md)
- [发布检查清单](docs/release-checklist.md)
