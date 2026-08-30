# clash-relay

[English](README.md)

`clash-relay` 是一个从零设计的、确定性且 fail-closed 的 Mihomo 配置生成项目。公开仓库只保存源码、示例和非敏感声明；真实生产订阅必须运行在 **Private 仓库** 中。用户只在跟踪的 YAML 中声明订阅元数据和少量策略，将真实订阅 URL 仅放入 GitHub Actions Secrets，CI 才会完成抓取、解析、能力分类、分层调度生成、静态验证、真实 Mihomo 验证和发布。

最终产物是标准 Mihomo `config.yaml`，FlClash 仅负责消费配置；设备端不需要 Python、数据库、ASN 数据库或守护进程。

> **敏感信息提示**：独立 Mihomo 配置会内联节点凭据，应视为最高敏感数据。严禁提交生成物。若 Public 仓库同时存在生产 `config.yaml` 和 `subscriptions.yaml`，发布工作流会在读取订阅 Secret 之前直接 fail-closed；只有 Private 仓库允许生成 credential-bearing candidate / Artifact。GitHub Release 与 Gist 默认关闭并带显式确认门禁。

## 五分钟开始

### 1. 为生产用途创建 Private 仓库

公开的 `clash-relay` 仓库用于源码和模板。真实订阅不要直接在 Public Fork 中运行。请从本项目创建一个 **Private 仓库**，再进行下面的生产配置。

### 2. 创建两个公开声明文件

```bash
cp config.example.yaml config.yaml
cp subscriptions.example.yaml subscriptions.yaml
```

编辑 `subscriptions.yaml`，为每个订阅设置唯一 `id` 和 `secret_name`。这两个 YAML 只能放策略和元数据，不得包含真实订阅 URL、Token、节点密码或其他私密端点。

### 3. 添加 GitHub Actions Secret

在 Private 仓库 Actions Secret 中创建 `CLASH_RELAY_SUBSCRIPTIONS`：

```json
{
  "SUB_PRIMARY": "<你的订阅 URL>",
  "SUB_SPECIAL": "<可选订阅 URL>"
}
```

工作流不会打印这个映射。生成 job 启动后，会先在内存中解析映射，并对每个独立 URL 执行 GitHub `::add-mask::`，再开始抓取订阅。这样即使底层工具意外输出某个派生 URL，也会进入 Runner 的独立脱敏集合。

### 4. 运行受保护的生产工作流

将 `config.yaml` 与 `subscriptions.yaml` 提交到 Private 仓库的 `main`，或手动运行 **Generate, validate, and publish**。在两个文件都存在之前，模板工作流会安全跳过，不会读取 Secrets 或生成 candidate。

如果两个生产声明文件存在，但仓库不是 Private，`prepare` job 会直接失败；candidate、Mihomo 生产验证和任何包含真实节点的 Artifact 都不会创建。

### 5. 获取结果

Private 仓库中的默认发布结果是版本化 Actions Artifact：

```text
clash-relay-production-<run-number>-<commit-sha>
```

其中的 `config.yaml` 包含节点凭据，请把 Artifact 的读取权限视为配置凭据权限。

GitHub Release 与 Gist 都不是默认交付路径。只有在明确理解敏感配置的可见性影响后才应开启；Gist 即使 unlisted 也不是私有存储。

## 核心数据流

```text
公开策略元数据 / Private Actions Secrets
  -> Public/Private 安全门禁
  -> 逐订阅 URL add-mask
  -> 受限订阅获取
  -> 不可信输入解析与清洗
  -> 国家和 capability 分类
  -> 业务资格筛选
  -> provider / AUTO / SERVICE-FALLBACK 分层生成
  -> Schema、引用、循环、空池和泄漏校验
  -> 真实 Mihomo 加载与启动验证
  -> Private Artifact / 显式可选 Release / 显式可选 Gist
```

业务层只暴露 `Proxy`、ChatGPT、Claude、Gemini、Google Play、视频下载以及显式启用的特殊线路。内部国家池、AUTO、fallback 和 provider 均设置为隐藏。可选池为空时进入 `REJECT`，必需池为空时构建失败，不会跨业务借线。

完整配置、开发、测试、CI/CD 和安全说明见英文主 README 及：

- [架构](docs/architecture.md)
- [配置模型](docs/configuration.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
