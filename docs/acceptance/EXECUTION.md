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
- 记录本次结果的文档提交 `d477547` 由 push run `39030768677` 以同样 job 集合覆盖，同样全绿；本记录自身的后续纯文档提交沿用该验证链，不再逐个列举 SHA。
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

## 2026-10-17：定向精简计划更新

- 输入：HEAD `4477383`，开始时工作区干净；承接本轮只读冗余评估，未修改业务代码、测试或工作流。
- 评估证据：`qualify_candidate.py` 向现有函数传入已删除的 `script_dir`，`inspect.signature(...).bind()` 实测抛参数不匹配错误，未执行 pipeline；serializer 与 `RuntimeGraph.candidate` 重复深拷贝；架构审计要求固定拷贝表达式；AST 对比发现发布 application 的 7 处 factory 函数体相同。
- 当前计划新增 S1 入口清理、S2 审计解耦、S3 重复实现消除，明确 S2→S3 依赖、行为保留范围与判别性验收。大范围观测模块重组暂不纳入首轮。
- 已完成的依赖检查移出待办，生产/KV 验证及按需 arm64/客户端验收继续保留。既有执行与归档记录不重写。
- 本次只交付计划；未实施代码精简、未运行全量业务测试、未触发远程工作流或生产操作。
- 验证：文档契约审计通过，当前三份文档的 15 个本地文件/锚点链接检查通过，`git diff --check` 无诊断；差异仅涉及 PLAN、README、EXECUTION。

## 2026-10-17：S1–S3 精简实施

- 输入：HEAD `4477383`，开始时工作区干净；本轮只改脚本、库代码与测试，未触发工作流或生产操作。

### S1 脚本入口收敛

- 全仓 `inspect.signature(...).bind()` 实测确认唯一不匹配点是 `scripts/qualify_candidate.py` 仍传 `script_dir`；该参数在 qualification pipeline 改为包内调用时已删除。
- 清点结果：无工作流、文档或测试调用该脚本；CLI 无等价子命令，因此保留入口（不能以仓库内无调用断言外部无人使用），只删除失效参数。
- 新增 `tests/test_qualify_candidate_script.py`（5 项）：把 adapter 的关键字实参绑定到真实 `run_qualification_pipeline` 签名、缺参退出码 2、pipeline 拒绝时保留 exit 2、成功时输出单个 JSON 对象。临时还原 `script_dir` 后该绑定用例实测 1 failed，恢复后 5 passed。

### S2 审计与测试解耦

- `scripts/audit_architecture_contract.py` 重写为 AST 判定：检查调用、参数、返回表达式和 `finally` 清理，不再匹配具体变量名或源码拼写。
- 等价改名/改写通过：serializer 参数改名、把深拷贝结果先赋给局部变量再返回、lifecycle 中 `binary`→`core` 改名、compiler 中 `graph`→`frozen` 改名，均判定通过。
- 负向样例仍失败：浅拷贝或直接返回 `graph.candidate`、lifecycle 阶段乱序、`finally` 清理缺失、release stage 调换 matrix 与 publish、Promotion Guard 丢失 `qualification_path`、qualification 退回字符串消息匹配或重新引入 `subprocess`、compiler 不再冻结 `RuntimeGraph`、builder 重新构造拓扑。
- 新增 `tests/test_architecture_audit_behavior.py`（15 项），上述正反样例各有用例。

### S3 重复实现消除

- serializer：`RuntimeGraph.candidate` 本身每次返回深拷贝快照，`serialize_runtime_graph` 不再二次 `copy.deepcopy(dict(...))`。实测修改返回值后原图、`group_members` 与原始候选均不变。
- 发布 application：7 处逐字相同的 `factory(key)` 收敛为 `_keyed_publisher(...)`；namespace 仍由各入口解析一次，派生键与凭据接线不变。
- metrics / proof：两处语义相同的准入计数清洗与 source-id 校验收敛到新模块 `admission_sanitize.py`；两个报告各自的字段白名单、失败分类和公开 JSON 未改。
- 兼容性：`test_production_metrics.py`、`test_production_proof.py`、application 失败路径、release transaction/recovery、KV 容量与 SLO 共 128 项定向回归通过。

### 整合验证

- Ruff 全仓通过，362 文件格式检查通过；类型门 58 文件 `Success: no issues found`；非集成全量 **1638 passed / 50 deselected**（32.05s 与 33.52s 两次）。
- `run_project_checks.py audit --offline-acl4ssr` 七项全部通过（仓库安全审计 482 文件），架构审计在真实仓库树上输出 `architecture contract audit: passed`。
- 覆盖率与真实 Mihomo 门未在本轮重跑：改动不触及生成字节与探针运行时，历史 84.32% 与内核矩阵结果不作为本轮证据。

## 2026-10-17：S1–S3 实施后复审

输入：HEAD `4477383` 加未提交 S1–S3 改动。读取差异并独立审查 adapter 接线、AST 审计、序列化隔离与共享工厂/清洗函数；仅更新验收文档，保留全部代码和测试改动。

| 本轮检查 | 实际结果 |
| --- | --- |
| `python -m pytest -m "not integration" -q -p no:cacheprovider --basetemp=.work/review-s1-s3-tests` | exit 0；1638 passed、50 deselected；55.61 秒 |
| Ruff check / format --check | All checks passed / 362 files already formatted；整体 exit 0 |
| `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；8 项审计子门通过，482 文件检查；ACL4SSR 在线核对 false |
| serializer 等价改写与隔离探针 | 三种安全写法均被审计拒绝，直接内部别名对照确实不隔离；聚合结果见 `.work/review-s1-s3/serializer-probes.json` |

**S1/S3 本地复审 PASS。** 失效参数已移除，adapter 以真实签名验证接线；serializer 返回已深拷贝的 `graph.candidate`，没有引入别名；factory 与清洗逻辑收敛保持既有参数/条件及报告白名单。未发现实际业务行为回归。

**S2 / P2：等价改写仍被误拒绝。** `audit_architecture_contract.py:141–194` 错把对 `graph.candidate` 快照再做浅拷贝视为不隔离；局部变量只追踪显式 deepcopy，普通快照转交也失败。实际创建嵌套 `RuntimeGraph`、执行各变体并修改返回值内部列表，结果如下：

| 写法 | 审计接受 | 修改返回值后原图保持不变 |
| --- | --- | --- |
| `return graph.candidate` | 是 | 是 |
| `snapshot = graph.candidate; return snapshot` | 否 | 是 |
| `return dict(graph.candidate)` | 否 | 是 |
| `return {**graph.candidate}` | 否 | 是 |
| `return graph._candidate`（真实内部别名对照） | 否 | 否 |

`test_architecture_audit_behavior.py:48、68` 反而把两个安全变体设成必须失败，故全套测试通过未揭示此问题。应改为行为隔离验证及正确的正反样例；当前生产 serializer 本身正确，不把审计误报描述成配置泄漏。

补充观察：新审计只检查 `qualification_path` 参数名，且不再检查 retryable 条件；改变路径或删除重试条件可通过该脚本。现有 `test_production_release_stage.py:45` 和资格拒绝行为测试仍分别约束对应行为，未据此断言整个 CI 丢失保护。后续应明确 AST 检查与行为测试的分工，避免为了补语法特判继续增大审计器（本批脚本 405 → 777 行）。

本轮未重采 coverage、未执行真实 Mihomo 或远程 CI，未触发生产动作。S2 的修正范围与验收已写回 PLAN；S1/S3 不因该独立审计问题重新打开。

## 2026-10-17：S2 审计误报修正

- 输入：HEAD `4477383` 加未提交 S1–S3 改动；本轮只修正 `scripts/audit_architecture_contract.py` 的 serializer 判据与对应测试，未改业务 serializer 本身。
- 根因：`_serializer_detaches_candidate()` 把「对 `graph.candidate` 的浅拷贝」当作未隔离。但 `RuntimeGraph.candidate` 每次访问都返回 `copy.deepcopy(self._candidate)`，从它派生的 `dict(...)`、`{**...}`、局部变量转交、再深拷贝都已与编译图分离。真正的别名只有访问私有 `graph._candidate`。
- 修正：判据改为 `_serializer_uses_public_snapshot()`——serializer 必须读取公开 `candidate` 快照，且不得读取任何下划线开头的图属性。AST 只保留这条依赖边界，不再枚举等价写法；嵌套隔离由行为测试承担。
- 行为测试：`tests/test_policy_compiler.py` 新增 `test_the_candidate_snapshot_isolates_every_derivation`，对五种派生写法分别修改返回值的嵌套列表并断言原图不变，另以 `_candidate` 对照确认真实别名会污染。
- 测试修正：删除两条把安全写法设为必须失败的用例；改为参数化的三种等价写法必须通过、局部转交必须通过、返回空映射必须失败、`graph._candidate` 必须失败。文件用例数 15 → 19。
- 分工说明：`_promotion_guard_keeps_qualification_input()` 与 `_audit_qualification()` 的 docstring 现已标明它们是结构性 tripwire；`qualification_path` 的实际传参与「仅 typed retryable 才重试」分别由 `tests/test_production_release_stage.py`、`tests/test_qualification_pipeline.py`、`tests/test_qualification_reliability.py` 承担行为保证。
- 复测结果与复审表一致：`return graph.candidate`、局部转交、`dict(...)`、`{**...}`、`deepcopy` 五种写法审计全部接受；`return graph._candidate` 被拒并报 `reads private graph state`。
- 验证：`tests/test_architecture_audit_behavior.py` + `tests/test_policy_compiler.py` 23 passed；非集成全量 **1643 passed / 50 deselected**（31.68s）；Ruff、格式（362 文件）、类型门（58 文件）、七项离线审计（482 文件）全部通过。审计脚本 777 → 759 行，未继续增加语法特判。
- 边界：未重采 coverage、未跑真实 Mihomo、未推送、未触发远程 CI 或生产动作。

## 2026-10-17：S2 误报修正后复审

输入：HEAD `4477383` 加既有未提交改动。当前业务 serializer 未再变化，新增修正集中于 AST 判据及隔离测试，因此本轮选择定向回归，未重复全量业务测试或 coverage。代码和测试文件均未修改，只更新验收文档。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest tests/test_architecture_audit_behavior.py tests/test_policy_compiler.py tests/test_runtime_graph.py tests/test_production_release_stage.py tests/test_qualification_pipeline.py tests/test_qualify_candidate_script.py -q -p no:cacheprovider --basetemp=.work/review-s2-final-tests` | exit 0；58 passed，0.88 秒 |
| Ruff check / format --check | 两项通过；362 files already formatted；整体 exit 0 |
| canonical typecheck | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；8 项子门通过，482 文件检查；ACL4SSR 在线核对 false |
| 新隔离测试的内部别名负向对照 | 临时 monkeypatch getter 返回 `self._candidate` 后，新测试仍通过；独立检查证实映射已被修改；退出码 0 |

**审计误报修复通过；P2 剩余项在行为测试。** `test_policy_compiler.py:189` 的 `build(_graph())` 将各 factory 绑定到图 A，循环中的 `graph = _graph()` 则新建图 B，断言检查的是 B。另一个独立问题是 `group_members()` 读取 `_groups` 索引，而构造函数将 `_groups` 和 `_candidate` 分别深拷贝，修改 `_candidate` 本就不一定改变该索引。因此即使把 factory 改为绑定同一图，仅保留该断言仍不足以证明 candidate 未污染。

本轮实际负向对照输出：

```json
{"new_isolation_test_with_live_alias":"passed","candidate_mapping_polluted":true,"separate_group_index_unchanged":true}
```

结果保存到 `.work/review-s2-final/isolation-negative-control.json`；monkeypatch 仅在内存中生效并已恢复。当前 `RuntimeGraph.candidate` 的深拷贝实现没有缺陷，本反例只证明新测试不能检测该实现退化，不声称当前生产候选已被污染。应绑定同一图并断言其 candidate 嵌套内容，保留原输入对照；将该负向对照固化为有判别力的测试验证。相邻 `test_mihomo_serializer_detaches_compiled_runtime_graph` 也应补 candidate 断言。

S1/S3 既有通过结论保持；本轮未发现新的业务实现回归，S2 行为验收暂不关闭。远程 CI、真实内核与生产/KV 操作均未执行。

## 2026-10-17：S2 隔离测试判别力修正

- 输入：HEAD `4477383` 加既有未提交改动；本轮只改 `tests/test_policy_compiler.py` 的隔离测试，未改 `RuntimeGraph` 与 serializer 业务实现。
- 复审指出的两个缺陷均成立：
  - `build(_graph())` 把各 factory 绑定到图 A，循环内 `graph = _graph()` 却新建图 B，断言检查的是 B。
  - `group_members()` 读构造时独立深拷贝的 `_groups` 索引，而污染发生在 `_candidate`，因此该断言即使绑定同一图也无法检测退化。
- 修正：
  - 派生表改为接受图的函数，每次循环用同一个 `graph` 生成快照并断言；断言改为读取 `graph.candidate["proxy-groups"][0]["proxies"]`，同时比对原始输入字典未被修改。
  - 新增 `test_extra_deepcopy_survives_a_leaking_snapshot_getter`：把公开 getter monkeypatch 成返回 `self._candidate`，断言只有额外 deepcopy 的派生保持隔离，其余四种必须泄漏。该用例把复审的临时负向对照固化为常驻测试，并保证正向测试具备判别力。
  - 新增 `test_the_private_candidate_is_the_live_mapping`，固定「私有映射即活文档」这一前提。
  - 相邻 `test_mihomo_serializer_detaches_compiled_runtime_graph` 补 `graph.candidate` 嵌套断言。
- 判别力实测：临时把 `RuntimeGraph.candidate` 改为 `return self._candidate` 后，`test_mihomo_serializer_detaches_compiled_runtime_graph` 与 `test_the_candidate_snapshot_isolates_every_derivation` 均失败（2 failed / 4 passed）；恢复后 6 passed。源码逐字节恢复。
- 验证：`tests/test_policy_compiler.py` 6 passed；非集成全量 **1645 passed / 50 deselected**（34.01s）；Ruff、格式（362 文件）、类型门（58 文件）、七项离线审计（482 文件）全部通过。
- 边界：未重采 coverage、未跑真实 Mihomo、未推送、未触发远程 CI 或生产动作。S2 至此关闭。

## 2026-10-17：S2 隔离测试修正后复审通过

输入：HEAD `4477383` 加既有未提交改动。本轮核对测试绑定对象、candidate 内容断言与负向对照，未修改代码/测试，只更新验收文档。

- 定向运行架构审计、policy compiler、RuntimeGraph、release stage、qualification pipeline 与脚本 adapter 六个测试文件：`60 passed in 0.88s`，exit 0（`--basetemp=.work/review-s2-isolation-final`）。
- Ruff check / format 通过（362 文件），canonical typecheck 通过（58 文件），离线审计 8 个子门通过（仓库 482 文件，ACL4SSR 在线核对 false）；各调用 exit 0。
- 内存负向对照：将 candidate getter 临时替换为内部映射，实际调用两个正向隔离测试，两者均抛 AssertionError，恢复后正常通过。此前“变更真实图却检查另一张图/另一份索引”的问题已修复。
- 独立正向探针：每次保留构造图所用的原始输入对象，五种派生及实际 serializer 均修改返回值的嵌套列表后，原始输入与同一个图的 candidate 均保持不变。
- 非阻断维护建议：新测试里的 `_source_candidate()` 原输入断言会重新生成模板，并非检查构造图时的原对象；建议后续直接保留该输入引用。关键同图断言已有判别力，且本轮独立探针验证了真实输入隔离，因此不重新打开已修复的 P2，也不把此建议表述为生产缺陷。

**结论：S1–S3 本地复审 PASS，未发现新的阻断问题。** 本轮仅做定向验证，未重复前一窗口的 1645 项全量测试或 coverage；远程 CI、真实内核及生产/KV 操作未执行，原有独立验收边界不变。

## 2026-10-17：S2 原输入断言修正

- 输入：HEAD `4477383` 加既有未提交改动；本轮只处理复审的非阻断建议，未改业务实现。
- 修正：`test_the_candidate_snapshot_isolates_every_derivation` 现在保留传给 `from_candidate` 的原始字典并在循环中比对，不再调用 `_source_candidate()` 重新生成模板。原写法比较的是两个新建对象，断言恒真。
- 判别力核实（如实记录）：把构造函数改为浅拷贝 `dict(candidate)` 或直接持有原引用 `candidate` 后，该原输入断言**仍然通过**。原因是 `from_candidate` 第一步就 `copy.deepcopy(dict(candidate))`，之后所有修改都作用于快照，原输入在任何现有实现路径下都不会被回写。因此这条断言固定的是「构造期不交出调用方对象」这一意图；真正的判别力来自同一用例中的两条 `graph.candidate` 断言，以及 `test_extra_deepcopy_survives_a_leaking_snapshot_getter`。
- 未据此声称新增检测能力；docstring 已写明该断言在当前实现下不会失败及其原因。
- 验证：`tests/test_policy_compiler.py` 6 passed；非集成全量 **1645 passed / 50 deselected**（30.83s）；Ruff、格式（362 文件）、类型门（58 文件）、七项离线审计（482 文件）全部通过。
- 边界：未重采 coverage、未跑真实 Mihomo、未推送、未触发远程 CI 或生产动作。

## 2026-10-17：原输入断言修正后复审确认

- 确认测试保留传给 `RuntimeGraph.from_candidate(source)` 的同一个 `source`，不再重建模板进行断言；业务实现未变化。
- 本轮定向执行 `test_policy_compiler.py`、`test_runtime_graph.py`、`test_architecture_audit_behavior.py`：30 passed，0.16 秒，exit 0；修改文件 Ruff/格式、架构审计、文档审计与 `git diff --check` 均通过。
- 内存负向对照仍有效：getter 临时返回内部映射时，两个正向隔离测试均检测到别名并抛 AssertionError；正常 getter 下原输入/快照断言通过。未修改源文件，monkeypatch 已恢复。
- 结论：PASS，上一轮非阻断建议已关闭，未发现新的需修复问题。未重复全量、coverage 或类型门；未运行远程 CI、真实内核及生产操作。本次仅更新验收记录，计划关闭状态保持。
