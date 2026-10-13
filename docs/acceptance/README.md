# clash-relay 后续整改与验收：当前入口

更新日期：2026-10-14。范围：承接本次项目检查的六项问题，并安排必要的测试与类型门禁优化。

**R01–R06 的源码修复、失败路径回归与本地验收门已完成（改动尚未提交）。** 本轮非集成测试 1453 passed、50 deselected，覆盖率 84.09%（floor 75%）；Ruff、类型（Windows 与 Linux 语义）、高风险覆盖率、离线审计与 doctor 全部通过；R04 已在真实 Mihomo v1.19.30 上取得 gRPC 传输证据。以上均为本地新运行，不代表 GitHub 工作流、真实 Cloudflare 或生产发布已验收。

## 只维护这三个文件

| 文件 | 职责 | 何时更新 |
| --- | --- | --- |
| [README.md](README.md) | 唯一当前状态、下一动作与导航 | 阶段状态或完成门变化时 |
| [PLAN.md](PLAN.md) | 唯一后续计划：顺序、依赖、改动范围与验收条件 | 实施方案或范围实质变化时 |
| [EXECUTION.md](EXECUTION.md) | 按执行窗口追加命令、实际结果、证据与交接 | 实际执行或复核后 |

以后沿用本目录，不新增日期版当前计划。日志、合成 fixture、测试报告放入新的 `.work/acceptance-<timestamp>/`，文档保留聚合结论与索引。样例只贡献结构；其中其他项目的设备、硬件与验收结果均不适用于 clash-relay。

## 当前状态（唯一汇总）

| 门 | 当前状态 | 完成证据或剩余条件 |
| --- | --- | --- |
| T0：共同基线与证据校准 | 完成 | 已记录 HEAD、输入清单（含 sha256）与本轮环境；反例已固化为回归 |
| T1 / R01：工作流权限 | 已实施，P1 | rollback、retention 增加 `actions: read`；权限兼容与 workflow 契约回归通过（111 passed） |
| T2 / R02：发布事务恢复 | 已实施，P2 | finalize 中断后相同候选幂等恢复；T2 定向 136 passed |
| T2 / R05：Cloudflare 提交状态 | 已实施，P2 | 缺失 / null / 错误类型 success 保留 CommitUnknownError，`success:false` 仍为明确拒绝 |
| T3 / R03：VMess 异常边界 | 已实施，P2 | 非有限数值与过深 JSON 归一为 SubscriptionError；skip / fail 与可选 / 必需源契约保持 |
| T3 / R04：VMess gRPC 保真 | 已实施（Windows 内核），P2 | 解析映射与真实 Mihomo v1.19.30 客户端流回归 2 passed；Linux 内核证据仍缺 |
| T4 / R06：Windows 类型检查 | 已实施，P3 | 3 个报错消失；Windows 与 Linux 语义类型门均 0 错误；CI 新增 `windows-platform` job（本地契约测试通过，远程未运行） |
| T5：最终整体验收 | 本地门通过，CI 未执行 | 本地 lint / 格式 / 类型 / 单测 + 覆盖率 / 高风险覆盖率 / 审计 / doctor 全绿；3.11–3.13 矩阵、wheel、双次生成与 stable-mihomo 完整矩阵沿用 CI |
| O1–O3：持续优化 | 已纳入计划 | O1 随缺陷修复落地；O2 随 T4；O3 分批推进，不阻塞六项整改交付 |
| GitHub / 生产现场验证 | NOT_EXERCISED | 未触发远程工作流，未访问生产 KV，未执行发布、回滚或清理 |

**下一动作：在精确 SHA 上提交并触发 GitHub CI（含新 `windows-platform` job 与 validated 聚合）与 stable-mihomo 矩阵；如需关闭完整协议验收门，补 Linux 内核 gRPC 证据。**

## 权威规范与证据导航

- [执行记录](EXECUTION.md)：本轮检查事实、复现方式、验证范围和样例备份。
- [发布契约](../publishing.md)、[Routing V2](../routing-v2.md)、[规则与来源策略](../rules.md)：保持为产品行为权威；本目录只管理整改与验收。
- [生产切换指南](../production-cutover.md)：后续外部验证的操作参考，不因本计划生成而执行。
- [客户端与 DNS 验证](../client-dns-verification.md)：真实客户端证据边界；本轮不以本地测试替代设备验证。

本计划交付的是后续工作安排，不代表发布就绪。源码检查通过、真实内核通过、远程工作流通过和生产状态分别记录。
