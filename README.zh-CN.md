# clash-relay

[English](README.md)

`clash-relay` 是一个从零设计的、确定性且 fail-closed 的 Mihomo 配置生成项目。用户只在公开 YAML 中声明订阅元数据和少量策略，将真实订阅 URL 仅放入 GitHub Actions Secrets，CI 就会完成抓取、解析、能力分类、分层调度生成、静态验证、真实 Mihomo 验证和发布。

最终产物是标准 Mihomo `config.yaml`，FlClash 仅负责消费配置；设备端不需要 Python、数据库、ASN 数据库或守护进程。

> **敏感信息提示**：独立 Mihomo 配置会内联节点凭据。严禁提交生成物。GitHub Release 与 Gist 默认关闭并带显式确认门禁。公开 Fork 中的 Artifact 也应视为敏感产物；真实使用更适合从本项目创建私有仓库。

## 五分钟开始

```bash
cp config.example.yaml config.yaml
cp subscriptions.example.yaml subscriptions.yaml
```

编辑 `subscriptions.yaml`，为每个订阅设置唯一 `id` 和 `secret_name`。在仓库 Actions Secret 中创建 `CLASH_RELAY_SUBSCRIPTIONS`：

```json
{
  "SUB_PRIMARY": "<你的订阅 URL>",
  "SUB_SPECIAL": "<可选订阅 URL>"
}
```

将公开 YAML 提交到 `main`，或手动运行 **Generate, validate, and publish**。在 `config.yaml` 与 `subscriptions.yaml` 都存在之前，模板工作流会安全跳过，不会读取 Secrets 或生成 candidate。默认从版本化 Actions Artifact 获取 `config.yaml`。

需要固定 URL 时，先在 `config.yaml` 中显式开启 GitHub Release，再设置两个仓库变量：

```text
PUBLISH_PUBLIC_RELEASE=true
CLASH_RELAY_PUBLICATION_ACKNOWLEDGEMENT=I_UNDERSTAND_THIS_PUBLISHES_PROXY_CREDENTIALS
```

地址形式为：

```text
https://github.com/OWNER/REPOSITORY/releases/latest/download/config.yaml
```

## 核心数据流

```text
公开配置 / Secrets
  -> 受限订阅获取
  -> 不可信输入解析与清洗
  -> 国家和 capability 分类
  -> 业务资格筛选
  -> provider / AUTO / SERVICE-FALLBACK 分层生成
  -> Schema、引用、循环、空池和泄漏校验
  -> 真实 Mihomo 加载与启动验证
  -> Artifact / 可选 Release / 可选 Gist
```

业务层只暴露 `Proxy`、ChatGPT、Claude、Gemini、Google Play、视频下载以及显式启用的特殊线路。内部国家池、AUTO、fallback 和 provider 均设置为隐藏。可选池为空时进入 `REJECT`，必需池为空时构建失败，不会跨业务借线。

完整配置、开发、测试、CI/CD 和安全说明见英文主 README 及：

- [架构](docs/architecture.md)
- [配置模型](docs/configuration.md)
- [安全模型](docs/security.md)
- [发布流程](docs/publishing.md)
- [首次发布检查清单](docs/release-checklist.md)
