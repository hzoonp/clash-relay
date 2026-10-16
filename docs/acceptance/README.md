# clash-relay 后续整改与验收：当前入口

更新日期：2026-10-17。范围：保留历史整改，记录当前工作区实施与独立复审结论。

**本批改动已提交并推送，远程 CI 全绿。** `a3b718b` 首次推送暴露 Windows 门缺陷（见下），修复提交 `32ad94b` 后 run `38952400087` 全部 10 个 job 通过（3.11 / 3.12 / 3.13、windows-platform、deterministic-generation、routing-required、stable-mihomo v1.19.29 与 v1.19.30、validated），`validated` SHA = `32ad94b`；记录本批结果的文档提交 `abc04aa` 由 run `38953501920` 以同样 10 个 job 覆盖。`deploy` 按 `github.event_name != 'push'` 跳过，未触碰生产。本记录自身的后续纯文档提交由同一验证链覆盖，不再逐个列举 SHA。O6 的报告留存由该 run 的真实 artifact 验证：6 个 artifact 均非空，quality 归档仅含两个白名单 XML，未包含配置、订阅或 fixture secret。证据见 [推送与远程 CI](EXECUTION.md#2026-10-17推送远程-ci-与-windows-门修复)。

**最新独立复审：PASS（本地整改范围）。** V01 的结果三态与 R12 的反向枚举已用原合成反例复核通过；92 项相关回归、Ruff、格式、58 文件类型门、离线审计与 `git diff --check` 均通过，未发现新的需修复问题。本轮采用定向验证，未重复全量 coverage；下述 1609 项及 84.32% 为实施窗口结果。远程 CI、真实内核及真实 KV 证据仍按各自边界保留，详见 [第四轮复审](EXECUTION.md#2026-10-17第四轮实施后复审通过)。

**2026-10-17 第三轮实施：R12 关闭；V01 的 P2 结果字段一致性已修复（本地闭环）。** 无新鲜度证据的陈旧读现在同时输出 `indeterminate / production_changed="unknown" / requires_manual_action=true`，不再同时声称“不确定”与“生产未改变”；显式日期断言后的 `not_committed / false`、已提交及候选原本已在线的规则保持不变。反例变体（改回 `false`）实测 6 failed / 1 passed，恢复后源文件与改动前逐字节相同（sha256 `d9a8bd23c365…`）。另把本批遗留的三个 CRLF 文件按 `.gitattributes`（`* text=auto eol=lf`）规范化为 LF，内容未变。§0.9 九门在代码、文档定稿、行尾规范化三个树状态各重跑一次，均 9 行退出码 0：1609 passed、50 deselected、覆盖率 84.32%、高风险模块 24/24、审计 475 文件、doctor `passed`。改动仍未提交、未推送。

最新依据见 [第三轮实施窗口](EXECUTION.md#2026-10-17第三轮实施窗口-v01-字段一致性)，上一轮复审见 [第三轮复审](EXECUTION.md#2026-10-17第三轮实施后复审)，历史实现证据见 [R12 / V01 实施窗口](EXECUTION.md#2026-10-17r12--v01-实施窗口)。

历史实施证据见 [执行窗口](EXECUTION.md#2026-10-16r07r11--o4o6-实施与-v01-状态机验证窗口)、[复审记录](EXECUTION.md#2026-10-16实施后独立复审)、[V01 / O6 / R12 / O5 实施窗口](EXECUTION.md#2026-10-16v01--o6--r12--o5-实施窗口) 与 [第二轮复审记录](EXECUTION.md#2026-10-17第二轮实施后独立复审)。

| 当前队列 | 状态 | 下一动作 |
| --- | --- | --- |
| R07：脱敏遇到畸形端口抛异常 | 已实施，P2 | 畸形 host/port 归入固定占位符；49 passed 定向回归 |
| R08：KV 派生键长度预检 | 已实施，P2 | 派生名集合按 UTF-8 字节预检，配置 / doctor / 发布入口共用；19 passed |
| R09：供应链审计结构漏检 | 已实施，P2 | 结构化扫描全部 `uses`（含 job 级与 `.yaml`）；回退脚本即 28 failed |
| R10：YAML 重复映射键 | 已实施，P2 | 严格 loader 拒绝重复键，config 与订阅共用；97 passed |
| R11：覆盖率比例 NaN | 已实施，P3 | 要求有限且 ∈ [0, 1]；23 passed |
| V01：陈旧读被判定为确定未提交 | **本地闭环（P1 → P2 两项均已实施）；真实服务证据独立开放** | 状态、人工复核标记、CLI 参数、恢复副本保留与 `production_changed` 三态字段均已对齐：陈旧视图输出 `indeterminate / "unknown" / true`，日期证据路径为 `not_committed / false`；反例变体 6 failed、公开入口探针首发与后续视图一致；真实 REST 读一致性 / 传播延迟未验证 |
| O4：发布退避真实等待 | 已完成 | 非集成整轮 100.41s → 27.69s，最慢用例 0.76s；重试 / 退避断言保留 |
| O5：依赖元数据 / 锁 / 安装一致性 | **已关闭（判定粒度），P3 余量开放** | `pip check` 改为逐 job / 安装环境判定；7 个删除检查的反例在修复前全部漏检，41 passed（33 → 41）。`publish.yml` / `rollback.yml` / `retention.yml` / `release-inventory-audit.yml` 缺 `pip check` 的加固仍在队列 |
| O6：CI 报告留存 | **已关闭（本地契约 + 真实 artifact），P2** | 8 个上传步骤开 `include-hidden-files: true`，XML 白名单、7 天保留与成功 / `if: failure()` 成对结构不变；glob 语义矩阵 18 条 + 10 变异用例，34 passed（10 → 34）。run `38952400087` 实测 6 个 artifact 全部非空，quality 归档仅含 `unit-3.12.xml` 与 `coverage-3.12.xml` |
| R12：恢复测试目录枚举顺序 | **关闭，P2** | run 用例改用 `_copy_names()` 排序比较；反向枚举回归覆盖生命周期 helper、生命周期与真实 `run()` 三条轨迹；未排序变体实测 1 failed，生产创建顺序未改 |
| §0.9 实施完成门 | 通过（本窗口 9 行退出码 0，三次） | 1609 passed / 50 deselected（77.04 / 135.56 / 134.30 秒），覆盖率 84.32%，高风险模块 24/24（`release_reconciliation.py` 92.65% / 门 90%），审计 475 文件，doctor passed |
| 本批改动的远程 CI | **通过（run `38952400087`，SHA `32ad94b`）** | 10 个 job 全部 success；首次推送的 `a3b718b` 失败于新增 Windows `pip check`，已在 `32ad94b` 修复并复验；O6 artifact 与 whitelist 由该 run 实测确认 |

本窗口命令、反例和证据边界见 [R12 / V01 实施窗口](EXECUTION.md#2026-10-17r12--v01-实施窗口)、[第三轮实施窗口](EXECUTION.md#2026-10-17第三轮实施窗口-v01-字段一致性) 与 `.work/acceptance-20261017-impl/`；上一窗口证据在 `.work/acceptance-20261016-01a/`。

**R01–R06 的源码修复、失败路径回归、本地验收门与远程 CI 已完成（以下为历史窗口数据，不代表当前工作区）。** 本地非集成测试 1456 passed、50 deselected，覆盖率 84.09%（floor 75%）。GitHub CI 在 `17ece93`、`a067828`、`99e7afb` 上连续三次全部 job 通过（3.11 / 3.12 / 3.13、`windows-platform`、deterministic-generation、routing-required、stable-mihomo v1.19.29 与 v1.19.30、validated），最新已记录的 validated SHA 为 `99e7afb`；R04 的 Linux（amd64）内核 gRPC 证据由 stable-mihomo 提供。首次推送暴露的 Windows 门缺陷（锁文件仅覆盖 Linux、步骤使用 bash 语法）已修复并回归通过。以上不代表真实 Cloudflare、生产发布或回滚已验收。

## 只维护这三个文件

| 文件 | 职责 | 何时更新 |
| --- | --- | --- |
| [README.md](README.md) | 唯一当前状态、下一动作与导航 | 阶段状态或完成门变化时 |
| [PLAN.md](PLAN.md) | 唯一后续计划：顺序、依赖、改动范围与验收条件 | 实施方案或范围实质变化时 |
| [EXECUTION.md](EXECUTION.md) | 按执行窗口追加命令、实际结果、证据与交接 | 实际执行或复核后 |

以后沿用本目录，不新增日期版当前计划。日志、合成 fixture、测试报告放入新的 `.work/acceptance-<timestamp>/`，文档保留聚合结论与索引。样例只贡献结构；其中其他项目的设备、硬件与验收结果均不适用于 clash-relay。

## 历史整改状态（R01–R06 / O1–O3）

| 门 | 当前状态 | 完成证据或剩余条件 |
| --- | --- | --- |
| T0：共同基线与证据校准 | 完成 | 已记录 HEAD、输入清单（含 sha256）与本轮环境；反例已固化为回归 |
| T1 / R01：工作流权限 | 已实施，P1 | rollback、retention 增加 `actions: read`；权限兼容与 workflow 契约回归通过（111 passed）。rollback / retention 自身未派发 |
| T2 / R02：发布事务恢复 | 已实施，P2 | finalize 中断后相同候选幂等恢复；T2 定向 136 passed |
| T2 / R05：Cloudflare 提交状态 | 已实施，P2 | 缺失 / null / 错误类型 success 保留 CommitUnknownError，`success:false` 仍为明确拒绝 |
| T3 / R03：VMess 异常边界 | 已实施，P2 | 非有限数值与过深 JSON 归一为 SubscriptionError；skip / fail 与可选 / 必需源契约保持 |
| T3 / R04：VMess gRPC 保真 | 已实施，P2 | 解析映射 + 真实内核客户端流回归；Windows v1.19.30 本地 2 passed，CI stable-mihomo 在 ubuntu-latest 上随 v1.19.29 / v1.19.30 各 2 passed。arm64 仍缺 |
| T4 / R06：Windows 类型检查 | 已实施，P3 | 3 个报错消失；CI `windows-platform` job 在 `17ece93` 上 success；并已实证该门失败会阻止 `validated` 与 `deploy` |
| T5：最终整体验收 | 本地门与远程 CI 均通过 | 本地 lint / 格式 / 类型 / 单测 + 覆盖率 / 高风险覆盖率 / 审计 / doctor 全绿；CI run `38561302259` 在 `17ece93` 上全部 job 通过，validated SHA = `17ece93` |
| O1–O3：持续优化 | O1 / O2 已落地，O3 两批均完成 | O1 随缺陷修复落地；O2 随 T4；O3 第一批把 URI / 订阅解析边界 7 个模块纳入类型门，第二批修掉 `util.py` 与 `dns_runtime_audit.py` 的两处类型错误、补 `types-PyYAML` 并纳入类型门（49 → 58 目标，零抑制），同时补一条 `UnsafeSubscriptionError` 精确类别回归 |
| GitHub 工作流验证 | 已完成 | run `38558938927`（失败，暴露 Windows 门缺陷）→ `38560429609`（安装与类型门通过）→ `38561302259`（`17ece93` 全绿）→ `38562845128`（`a067828` 全绿）→ `38565198668`（`99e7afb` 全绿，56 目标类型门） |
| 生产现场验证 | NOT_EXERCISED | 未触发 `deploy`（push 事件按设计不部署），未访问生产 KV，未执行发布、回滚或清理 |

历史外部验证待办仍保留：观察定时发布能否取用精确 SHA 的验证结果；如需 arm64 协议证据，再扩展 stable-mihomo 矩阵。本轮下一动作以页首新增队列为准。

## 权威规范与证据导航

- [执行记录](EXECUTION.md)：本轮检查事实、复现方式、验证范围和样例备份。
- [发布契约](../publishing.md)、[Routing V2](../routing-v2.md)、[规则与来源策略](../rules.md)：保持为产品行为权威；本目录只管理整改与验收。
- [生产切换指南](../production-cutover.md)：后续外部验证的操作参考，不因本计划生成而执行。
- [客户端与 DNS 验证](../client-dns-verification.md)：真实客户端证据边界；本轮不以本地测试替代设备验证。

本计划交付的是后续工作安排，不代表发布就绪。源码检查通过、真实内核通过、远程工作流通过和生产状态分别记录。
