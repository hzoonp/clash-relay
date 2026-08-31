# clash-relay

[English](README.md)

`clash-relay` 是一个面向 Mihomo / FlClash 的确定性配置生成与场景调度项目。多个私密订阅在 GitHub Actions 中获取、清洗、分类和合并，最终生成 standalone `config.yaml`。公开仓库只保存策略元数据；真实订阅 URL 和最终节点凭据不会进入 Git 历史。

> **敏感信息提示**：最终 `config.yaml` 内联真实节点凭据，应视为最高敏感数据。生产工作流只把通过验证的配置写入私有 Cloudflare Workers KV，不上传到公开 Artifact、Release、Gist、commit 或 Pages。

## 从 Fork 开始

全新 Fork 请直接使用 [Fork 快速上手](docs/quickstart.zh-CN.md)：

```text
Fork
  -> 配置 CLASH_RELAY_SUBSCRIPTIONS
  -> 配置 Cloudflare KV Secret / Variables
  -> 手动 dry-run（publish=false）
  -> 查看聚合 production proof
  -> publish=true
  -> 必要时执行双核验证的生产回滚
```

生产链还包含网页浏览实时资格分层、私有匿名历史调度、OpenAI/Claude/Gemini 独立资格检测、端到端 source reachability 审计、ACL4SSR Online parity，以及 Mihomo v1.19.30 / v1.19.29 双版本验证。不同的生产 bytes 在替换前会先私有保存 previous-good。

## 当前公开场景

FlClash 只暴露六个主要用户决策：

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

ACL4SSR compatibility groups、地区 helper、自动 scheduler 和 qualification runtime groups 都保持隐藏；公开 selector 不直接挂载 proxy provider，因此不会展开大量 raw runtime 节点。

## Source Policy

合并多个订阅不代表所有订阅可以进入所有场景：

```text
SUBSCRIPTION_1_URL
  ├─ >2x                 -> 剔除
  ├─ EMBY 节点           -> 剔除
  ├─ browsing            -> 允许
  ├─ ai                  -> 允许
  └─ general/media/...   -> 禁止

SUBSCRIPTION_2+
  ├─ general
  ├─ browsing
  └─ ai
```

核心不变量：

1. `subscription_1` 只允许 `browsing` 和 `ai`，不能进入 `general`。
2. `subscription_1` 中名称包含 `EMBY`（大小写不敏感）的媒体专用节点在库存生成前剔除。
3. 明确标注倍率 `> 2x` 的节点在分类、去重和 provider 生成前剔除；恰好 `2x` 和无倍率标记节点保留。
4. `流媒体`、`消息通讯`、`下载流量`、ACL compatibility selector 和最终 `MATCH` 都只能使用 general inventory，因此无法触达 `subscription_1`。
5. source reachability 在 qualification 前后都重新审计；不能靠 UI 或用户“不去点某节点”维持隔离。

## ACL4SSR Fidelity

`rules/acl4ssr.yaml` 固定：

```text
repository: ACL4SSR/ACL4SSR
ref: c498ae4911f15b19c5ceaef6f8737ca8705b4430
reference: Clash/config/ACL4SSR_Online.ini
```

P10 以后，**ACL4SSR Online 负责分类语义，clash-relay 负责安全的节点库存和调度**。仓库保存同一 immutable ref 的 Online reference，并由 CI/生产审计机械比较，而不是再手工重新解释 ACL4SSR 的规则主干。

Canonical 分类顺序：

```text
LocalAreaNetwork -> 全球直连
UnBan            -> 全球直连
BanAD            -> 广告拦截
BanProgramAD     -> intentionally disabled
GoogleFCM        -> 谷歌FCM
GoogleCN         -> 全球直连
SteamCN          -> 全球直连
Microsoft        -> 微软服务
Apple            -> 苹果服务
Telegram         -> 消息通讯

AI / OpenAI      -> 人工智能    # clash-relay 扩展
ProxyMedia       -> 流媒体
Download         -> 下载流量    # clash-relay 扩展
ProxyLite        -> 网页浏览
ChinaDomain      -> 全球直连
ChinaCompanyIp   -> 全球直连
GEOIP,CN         -> 全球直连
MATCH            -> 漏网之鱼
```

不再使用之前人为替换的 `ProxyGFWlist` 作为 canonical browsing classifier，也不再插入 YouTube/Netflix/Game/Bilibili/ChinaMedia 等独立规则去改变 ACL4SSR Online 的主分类优先级。

### 唯一明确的差异

- `BanProgramAD.list / 应用净化` **保持关闭**，因为已经确认会误杀手机端图片/CDN；基础 `BanAD.list` 继续启用。
- AI/OpenAI 在 `ProxyMedia` 前提前截获，避免 AI 域名被宽泛媒体规则吞掉。
- `Download.list` 在 `ProxyLite` 前进入 `下载流量`。
- ACL4SSR 的单订阅 raw-node `.*` wildcard 不直接复制；改由 source-aware selector 注入，防止 `subscription_1` 泄漏到 general 场景。

任何新的分类差异都必须显式写入 fidelity contract，否则 CI 和生产审计 fail closed。

## 隐藏的 ACL compatibility selectors

默认成员顺序保持 ACL4SSR Online 语义：

```text
全球直连: DIRECT -> 代理选择 -> 自动选择
广告拦截: REJECT -> DIRECT
谷歌FCM: 代理选择 -> 全球直连 -> 自动选择
微软服务: 全球直连 -> 代理选择
苹果服务: 代理选择 -> 全球直连
漏网之鱼: 代理选择 -> 全球直连 -> 自动选择
```

这些组保持 hidden，不增加 FlClash 顶层 UI 噪声。

## 网页浏览地区调度

`ProxyLite -> 网页浏览` 继续使用 P8 的 browsing inventory 和实时资格检测：

```text
网页自动
  ├─ US Stable -> US Reserve
  ├─ SG Stable -> SG Reserve
  ├─ JP Stable -> JP Reserve
  ├─ TW Stable -> TW Reserve
  ├─ KR Stable -> KR Reserve
  ├─ HK Stable -> HK Reserve
  └─ OTHER Stable -> OTHER Reserve
```

默认自动地区顺序：

```text
US -> SG -> JP -> TW -> KR -> HK -> OTHER
```

自动模式只有整个优先地区不可用才跨地区；手动地区选择不会偷偷跨区。历史降级只在本地区将当前合格节点从 Stable 移到 Reserve，不会将其从自动容灾资格中删除。

## AI 实测资格

AI inventory 与 browsing/general 独立。香港在 AI qualification 前硬排除，OpenAI、Claude、Gemini 分别通过候选节点实测并独立 fail closed，地区偏好为：

```text
US -> SG -> JP -> TW -> KR -> OTHER
```

某服务没有合格节点时不会回退到未经验证节点。

## 数据流

```text
GitHub Secrets
  -> 多订阅获取
  -> 安全解析 / 标准化
  -> source admission（allowed_uses / EMBY / >2x）
  -> 去重 / 国家分类
  -> general / browsing / ai inventories
  -> ACL4SSR Online classification + AI/Download 两项扩展
  -> browsing qualification + regional Stable/Reserve
  -> OpenAI / Claude / Gemini qualification
  -> post-qualification reachability + ACL fidelity audit
  -> Mihomo v1.19.30 + v1.19.29
  -> previous-good snapshot
  -> Cloudflare Workers KV
  -> FlClash
```

## GitHub Secrets

推荐只保存一个订阅映射 Secret：

```text
CLASH_RELAY_SUBSCRIPTIONS
```

值可以是 JSON：

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

Cloudflare KV 发布还需要：

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

不要把真实订阅 URL 写入 tracked YAML、README、Actions 参数或日志。

## 验证契约

CI/生产门禁直接锁定：

```text
subscription_1 -> general/media/messaging/download/final -> 禁止
subscription_1 -> browsing/ai                         -> 允许
subscription_1 EMBY                                   -> 剔除
subscription_1 >2x                                    -> 剔除

ProxyMedia -> 流媒体
Telegram   -> 消息通讯
Download   -> 下载流量
ProxyLite  -> 网页浏览
MATCH      -> 漏网之鱼
BanProgramAD -> disabled
```

并执行 pinned ACL4SSR upstream/vendored parity、Ruff、Python 3.11/3.12 单元测试、仓库安全审计、确定性生成、Routing V2 Drift Guard，以及 Mihomo v1.19.30 / v1.19.29 真实启动集成测试。

## 本地开发

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/audit_acl4ssr_fidelity.py
python scripts/repository_audit.py
```

## 文档

- [Fork 快速上手](docs/quickstart.zh-CN.md)
- [Fork quickstart](docs/quickstart.md)
- [配置模型](docs/configuration.md)
- [架构](docs/architecture.md)
- [ACL4SSR 规则模型](docs/rules.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [版本与兼容性](docs/versioning.md)
- [首次发布检查清单](docs/release-checklist.md)
