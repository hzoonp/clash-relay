# 当前执行记录

当前任务见 [PLAN](PLAN.md)，2026-10-13 至 2026-10-17 的整改与验证全过程见[历史执行记录](archive/2026-10-17/EXECUTION.md)。本文件仅追加新一轮实际执行结果。

## 2026-10-17：历史归档与计划精简

- 输入：HEAD `0e82624bbd1e772eb44978d673f27687c63490b5`，开始时工作区干净。
- 将原 README、PLAN、EXECUTION 归档到 `archive/2026-10-17/`；正文与记录保留，仅新增历史说明并修正相对链接。还原这两类机械调整后，三份文件逐字节等同原文件，已实际验证。
- 当前计划仅保留生产/KV 验证、运行时工作流依赖检查及按需设备验收；已完成的代码整改和远程 CI 不再列为待办。
- 本次仅修改文档，未运行业务全量测试、远程 CI 或生产操作；旧 run 与覆盖率均以历史证据标注。
- 验证：文档契约审计通过，仓库安全审计通过（479 文件），49 个本地 Markdown 文件/锚点链接全部有效，`git diff --check` 无诊断；当前 PLAN 精简为 21 行。

## 2026-10-17：生产工作流依赖检查（P3）

- 输入：HEAD `0e82624bbd1e772eb44978d673f27687c63490b5`，与 `origin/main` 一致。
- 四份生产工作流在安装运行时锁的同一 job 内各补一步独立的 `Verify installed dependency consistency`（`python -m pip check`）：`publish.yml`（`deploy`）、`rollback.yml`（`rollback`）、`retention.yml`（`retention`）、`release-inventory-audit.yml`（`audit`）。四者均在 `ubuntu-latest` 上、于 hash 校验安装之后、任何业务步骤之前执行。
- `scripts/audit_supply_chain.py`：`_audit_ci_pip_checks()` 改名并改为 `_audit_pip_checks()`，判定范围从 `ci.yml` 扩展到全部工作流；同时拒绝不能失败的检查——`continue-on-error: true` 或 `if: false` 的 `pip check` 步骤不算满足门禁。判定仍是逐 job / 逐解释器，别的 job 或别的（先运行的）工作流不代偿。
- runner 预装包核对：`ubuntu-latest` 的 `actions/setup-python` 解释器未出现 Windows 镜像那种工具污染；历史 run `38948496539` 的失败只发生在 `windows-latest`，其余 6 个共享解释器的 job 未被同样污染，因此四份生产工作流不需要额外 venv 隔离。`windows-platform` 的自有 venv 隔离保持不变。
- 未改任何依赖版本：`requirements.lock`、`requirements-dev.lock`、`pyproject.toml` 均未改动。
- 反例：新增 4 个「删除某份生产工作流的 pip check」用例、1 个「ci.yml 的检查不覆盖生产安装」用例、1 个「跨 job 代偿」既有用例，以及 2 个「检查不能失败」用例（`continue-on-error: true`、`if: false`），全部按预期抛错；正常仓库树通过审计。
- 验证：`pytest tests/test_supply_chain_audit.py` → 50 passed；非集成全量 1618 passed / 50 deselected（31.66s）；`audit_supply_chain.py`、`audit_documentation_contract.py`、`repository_audit.py` 均通过；Ruff、格式与单文件 mypy 通过。
- 边界：本轮只做本地实现与定向验证，未推送、未触发远程 CI；生产工作流的实际 `pip check` 步骤尚未在 Actions 上运行过。

### 2026-10-17 推送与远程 CI

- 提交：代码 `942c397`、文档 `bc29fcd`，已推送到 `origin/main`（`0e82624..bc29fcd`）。
- push run `39030406944`（SHA `bc29fcd`）全部通过：Python 3.12 quality、3.11 / 3.13 compatibility、windows-platform、deterministic-generation、routing-required、stable-mihomo v1.19.29 与 v1.19.30、validated 均 success；`Run canonical production lifecycle` 按 `github.event_name != 'push'` 跳过，生产未被触碰。
- 实际日志确认：quality job 输出 `supply-chain audit: passed`（扩展后的逐工作流判定在真实仓库树上通过），各 job 的 `pip check` 均返回 `No broken requirements found.`。
- 仍未在 Actions 上执行：四份生产工作流新增的 `pip check` 步骤只有在下次 manual dispatch / schedule / rollback / retention / inventory 运行中才会真正跑到；本次 push 不进入生产生命周期。

## 2026-10-17：生产链路只读核对（P1 准备阶段）

- 只读核对，未触发任何 workflow、未写 KV、未改仓库变量或 Secret。
- 远程状态：`publish.yml` 最近 4 次定时运行（`38967415964`、`39002669535`、`39026145103`，以及更早的成功运行）与最近一次手动 `workflow_dispatch`（`39028946968`）均为 success，全部绑定 SHA `0e82624`；手动运行的 `publish=true`，其余 `CLASH_RELAY_SCHEDULE_PUBLISH` 取默认（`hzoonp/clash-relay` 且变量未设为 `false`）。
- validated SHA 链：`resolve_validated_sha.py` 在每次运行中解析到 `0e82624`，复用 run `38954374691`（`main` push 的完整验证）；`deploy` 的 checkout 与 `git rev-parse HEAD` 均等于该 SHA，`CLASH_RELAY_VALIDATED_SHA` 一致。
- 发布结果：`39028946968` → `publication_status=published`、`release_phase=verified`、`release_id=d9f29e28…e5314`（等于 `config_sha256`）；`39026145103` → `release_id=84dcb98d…bf8e`；`39002669535` → `release_id=cbd2c698…9135`。三次的 `promotion_guard`、`mihomo_matrix`、`manifest_status`、`proof_status` 均为 passed，`derived_state=completed`，observability 三项均为 published。
- 失败运行（同一 SHA 早期）：`38933766322`（2026-10-16）在 `browsing_qualification` 以 `qualification_failure_category=configuration` 失败；`38682716268`（2026-10-15）被 `promotion_guard` 以 `reason=degraded` 阻断（AI 组 0 节点、provider 比例 0.4737）。两次都在激活前停止，之后的重试成功，符合 fail-closed 设计。
- **未验证项（明确保留）**：`rollback.yml` 与 `retention.yml` 从未运行过（run 列表为空）；`release-inventory-audit.yml` 最近一次运行 `36726164908`（2026-09-30）失败，输出 `status=recoverable_interrupted_commit`、`transaction=pending`、`mutation=none`，保护集为 `7b71e735…`、`a1be091d…`、`cca45cb1…`。该观测已过期三周，后续多次成功发布表明事务已推进，但本轮没有 Cloudflare 凭据，无法重新读取当前指针做确认。
- 结论：P1 的只读准备完成；dry-run、发布、回滚、retention 演练仍需按生产切换指南逐项授权后执行，未执行项不视为验收通过。
