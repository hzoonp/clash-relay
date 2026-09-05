# Fork 配置边界

普通 Fork **不需要先理解整个内部架构**。先使用最小、受支持的配置面；只有遇到明确需求时，才进入高级 policy 文件。

## 普通 Fork 只需要负责三件事

1. **订阅凭据**：真实 URL 只放在仓库 Secret `CLASH_RELAY_SUBSCRIPTIONS` 中。
2. **订阅策略**：只有启停订阅、改变允许场景、国家边界、名称准入规则或倍率上限时，才修改 `subscriptions.yaml`。
3. **生产发布**：配置 Cloudflare KV Secret / Variables，先 `publish=false` dry-run，再有意执行正式发布。

其余内容都有安全默认值。日常 Fork 不需要修改 qualification 内部实现、scheduler history、release transaction、RuntimeGraph 代码或 Mihomo 版本固定项。

## 不同需求应该改哪里

| 目标 | 主要修改面 | 通常不要动 |
| --- | --- | --- |
| 增删订阅 | `subscriptions.yaml` + `CLASH_RELAY_SUBSCRIPTIONS` | routing、scheduler、release 代码 |
| 限制某订阅能进入哪些场景 | `subscriptions.yaml` → `allowed_uses` | 运行时自动生成的 proxy groups |
| 剔除某订阅中特定名称节点 | `subscriptions.yaml` 的 admission 字段 | capability 分类规则 |
| 限制明确倍率节点 | `subscriptions.yaml` → `max_node_multiplier` | selector 排序 |
| 修改公开路由行为 | `policies/routing.yaml` | 订阅凭据 |
| 修改 pool / 地区拓扑 | `policies/topology.yaml` | production release transaction |
| 修改 qualification 语义 | 只有明确重设计健康规则时才改 qualification policy | Cloudflare 发布代码 |
| 修改定时刷新频率 | `.github/workflows/publish.yml` | candidate 生成语义 |
| 恢复上一生产版本 | 手动 rollback Workflow | 不要手工修改 KV key |

## 默认订阅源策略

Canonical 仓库有意把 `subscription_1` 与其它订阅区分处理：

```text
subscription_1
  allowed uses: browsing, ai
  明确倍率 > 2x: 剔除
  恰好 2x: 保留
  EMBY 标记节点: admission 阶段剔除

subscription_2+
  allowed uses: general, browsing, ai
```

`ingest_order` 只用于保证订阅摄入和输出的确定性，不是节点质量分数，也不能当作路由优先级。

## 高级配置面

只有需求明确涉及以下能力时才修改：

- `policies/routing.yaml`：场景绑定和公开路由行为；
- `policies/topology.yaml`：pool、地区、probe 与 source-use selector；
- `policies/scheduling.yaml`：scheduler policy；
- classification fragments：国家和 capability 语义；
- `promotion-guard.yaml`：发布可用性/退化阈值；
- `tools/mihomo-versions.json`：经过验证的 stable/prerelease core matrix。

修改高级配置仍必须通过相同的 fail-closed 校验和生产生命周期；不要为了让某项策略更容易生效而绕过这些门禁。

## 这些内容不能当成用户配置去编辑或发布

- 带代理凭据的生成后生产 `config.yaml` 字节；
- 受版本控制文件中的真实订阅 URL 或订阅 payload；
- 私有 qualified candidate；
- 节点级 qualification 结果；
- 手工编辑的 KV release object 或 pointer。

修改公共声明后先运行 `clash-relay doctor --public-only`，随后执行私有 doctor 检查；首次正式发布前再完成一次 `publish=false` production dry-run。
