# clash-relay 后续统一计划

建立日期：2026-10-13；最新复查：2026-10-17。当前状态只见 [README](README.md)，实际执行与证据只追加到 [EXECUTION](EXECUTION.md)。历史实施方案及记录保留，最新剩余队列见 §0.1b。

## 0. 当前待办：2026-10-16 项目复查

审查基线：`cd9c1788db110d1644d47f9c22f22b546f6c2524`，开始时工作区干净。历史 R01–R06、O1–O3 已关闭；下文第 1–9 节保留原整改方案作为追溯依据，不重新打开历史事项。本节是本轮新增实施队列。

本轮检查覆盖声明加载、订阅构建、运行图、脱敏、发布与恢复、工作流、依赖和验证脚本。非集成测试 1456 passed、50 deselected；Ruff、58 个文件类型门、离线审计及公共 doctor 均通过。这些结果与下面的边界缺陷并不矛盾：现有测试和审计未覆盖相应反例。本轮未重跑覆盖率、真实 Mihomo 集成、其他 Python/系统矩阵或远程 CI，也未验证生产状态。

### 0.1 优先级与顺序

P1 表示优先确认的生产恢复风险；P2 表示应修复的边界或门禁缺口；P3 表示低概率异常加固。优化项单独排期，不作为已有生产故障。

| 编号 | 类型 / 优先级 | 结论 | 实施顺序 |
| --- | --- | --- | --- |
| V01 | 待验证风险 / P1 | 旧读视图可能被当作“确定未提交” | 先建立状态模型与恢复证据回归，再决定语义调整 |
| R07 | 已复现 / P2 | 非法 URL 端口让脱敏函数抛出 ValueError | 第一批：诊断与输入边界 |
| R08 | 已复现 / P2 | 配置允许的 KV 基键长度超过派生发布键容量 | 第一批：发布前预检 |
| R09 | 已复现 / P2 | 供应链审计漏查命名 step 等工作流写法 | 第二批：门禁正确性 |
| R10 | 已复现 / P2 | YAML 重复键静默覆盖，声明歧义未被拒绝 | 第一批：输入边界；先明确订阅兼容语义 |
| R11 | 已复现 / P3 | 风险覆盖率审计接受 NaN | 第二批：门禁正确性 |
| O4 | 优化 / P2 | 非集成测试等待真实退避时间 | 与发布回归同行，保持重试断言 |
| O5 | 优化 / P2 | 依赖元数据、锁文件与安装环境缺少一致性门 | 第三批：开发与构建可靠性 |
| O6 | 优化 / P3 | CI XML 报告未持久保存 | 第三批：诊断体验 |

R07/R08/R10 可分开实施；R09/R11 可共享审计负向 fixture。O4 涉及发布测试，应与 V01 的测试修改统一整合。每项先固化反例，再改实现，最后做对应回归；本节编制阶段不改业务代码。

**实施状态（2026-10-16 实施窗口，改动在工作区、未推送）：**

| 编号 | 状态 | 实施结果与定向回归 |
| --- | --- | --- |
| R07 | 已实施 | 畸形 host/port 归入固定占位符；49 passed |
| R08 | 已实施 | 新增 `src/clash_relay/kv_keys.py` 派生键字节预检（配置 / doctor / 发布入口共用）；19 passed；未改 Schema 字符上限 |
| R09 | 已实施 | 结构化扫描全部 `uses`；33 passed，脚本回退到 HEAD 时 28 failed |
| R10 | 已实施 | 严格 loader 拒绝重复键，config 与订阅共用；97 passed |
| R11 | 已实施 | 比例须有限且 ∈ [0, 1]；23 passed |
| O4 | 已完成 | 非集成整轮 100.41s → 27.69s，最慢用例 0.76s；重试 / 退避断言保留 |
| O5 | 已完成 | 元数据 ↔ 双锁比对、ci.yml 两处 `pip check`、缓存键覆盖全部已装锁；并修复真实缺陷（`requirements-dev.lock` 缺 Windows-only `colorama`） |
| O6 | 已完成 | 8 个白名单归档步骤（成功 / `if: failure()` 成对，7 天），重试报告按 attempt 区分；10 passed |
| V01 | **维持待验证** | 状态机已证明陈旧视图可达且会丢弃唯一私有副本，并留下回归护栏；真实读一致性与生产故障注入仍缺，不写为已修复 |

### 0.1a 实施后复审：剩余队列（2026-10-16）

上面的“实施状态”保留为实施窗口记录；最新结论以下表及 README 为准。复审没有修改业务代码或测试。

| 项目 | 优先级 / 当前结论 | 后续动作与验收 |
| --- | --- | --- |
| V01 | P1 条件性风险，继续开放 | 将现状刻画测试与修复验收区分；在已知写结果不明的上下文中，旧读值不足以独立证明未提交。补 `ProductionPipeline.run()` 的异常→保留→finally 全链路测试，判定不明时应保留恢复证据；确定拒绝与已提交仍分别处理。真实 REST 读取可达性另列，不将 fake 结论写成生产事故 |
| O6 | P2，重新打开 | `.github/workflows/ci.yml:329–343` 两个 integration 上传步骤允许隐藏文件搜索，或将报告迁出隐藏目录；保持具体 XML 白名单。使用固定 action 对应的 glob 规则验证匹配集，补失败路径和三次 attempt 的匹配回归，再在后续 CI 检查真实 artifact |
| R12 | P2，新增跨平台测试缺陷 | `tests/test_stale_read_reconciliation.py:271` 对 `iterdir()` 结果排序或集合比较；以不同合法枚举顺序验证相同内容均通过，并在 Linux/Windows 相关门执行。不要调整生产文件创建顺序来迎合测试 |
| O5 护栏 | P3，局部未完成 | `scripts/audit_supply_chain.py:286–296` 的 `pip check` 判定改为按相关 job / 安装环境，而非全工作流命令集合；分别删除 Windows、compat、stable-mihomo、quality 的检查均应触发审计失败，同时允许其他 job 保留检查 |

V01 证据校正：第二次发布的 production 本来就处于旧读状态，current 尚未更新，此时仅隐藏 transaction 即可误判，不要求已更新的 pointer 再回退。首次发布切换到空旧视图也会误判，现有“首次发布不会误判”的结论只适用于 pending intent 可见的测试轨迹。当前测试仅调用 `_preserve_ambiguous_release()`，证明“不生成 recovery 副本”；最终清理来自 `run()` 的静态代码证据，尚非贯穿执行证据。现有 `NOT_COMMITTED` / 空副本断言记录缺陷现状，修复时应调整期望，不能拿它们证明风险已关闭。

R07–R11、O4 本轮未发现新的实现回归；它们可独立验收，不必与所有后续优化绑定。全部计划关闭仍需处理上表，且本地、远程 CI 和真实服务证据分别记录。

**实施状态（2026-10-16 §0.1a 实施窗口，改动在工作区、未推送；逐项证据见 [EXECUTION 实施窗口](EXECUTION.md#2026-10-16v01--o6--r12--o5-实施窗口)）：**

| 项目 | 状态 | 实施结果与定向回归 |
| --- | --- | --- |
| V01 | 本地语义已落地，**仍独立开放** | `_preserve_ambiguous_release()` 只对已证实的 `committed` 释放恢复副本，`not_committed` / `unknown` / `read_failed` 一律保留候选与旧值并写 `reconciliation_status`；诊断白名单同步移除 `not_committed`。新增 `run()` 异常→保留→`finally` 三条轨迹（判定不明 / 已提交 / 确定拒绝）与生命周期级验收回归；14 passed（8 → 14）。真实 REST 读可达性、传播延迟与生产故障注入仍缺，不写为已修复 |
| O6 | **关闭** | 8 个上传步骤开 `include-hidden-files: true`（`:108`、`:119`、`:155`、`:164`、`:212`、`:221`、`:340`、`:351`），白名单保持 `.work/*.xml`、7 天保留与成功 / `if: failure()` 成对结构；契约测试按固定 action 的 glob 规则计算匹配集，冻结 18 条语义矩阵与 10 个变异用例；34 passed（10 → 34）。真实 CI artifact 检查待推送后单独执行 |
| R12 | **关闭** | `_copy_names()` 排序比较 + 反向枚举回归；生产文件创建顺序未改；同文件 14 passed |
| O5 护栏 | **关闭（判定粒度）；生产工作流 pip check 仍开放** | `_audit_ci_pip_checks()` 逐 job / 逐解释器判定，删除任一相关 job 的 `pip check` 或跨 job 代偿均失败（7 个反例在修复前全部 DID NOT RAISE）；41 passed（33 → 41）。`publish.yml` / `rollback.yml` / `retention.yml` / `release-inventory-audit.yml` 的 `pip check` 加固未纳入本窗口 |

§0.9 实施完成门在同一工作区内容上重跑，9 行全部退出码 0：1605 passed、50 deselected（102.04 秒）、覆盖率 84.32%（floor 75%）、24 个高风险模块全部达标、审计仓库检查 475 文件、doctor `passed`、供应链审计 `passed`；文档更新完成后的最终树再次运行同一脚本（run B）仍为 9 行退出码 0、用例数与覆盖率相同（83.11 秒）。用例数由复审窗口的 1567 增至 1605（+38）。改动未提交、未推送；远程 CI 与真实服务结论仍分别待记录。

### 0.1b 第二轮实施后复审：剩余队列（2026-10-17）

本节覆盖前一实施窗口的关闭结论。O6 隐藏文件匹配、O5 逐 job / 解释器检查和 V01 生命周期恢复副本保留均通过本地复审，未发现新的实现回归；真实远程结果仍分别记录。保留两项待办：

| 项目 | 复审结论 | 后续动作与验收 |
| --- | --- | --- |
| R12 / P2 | 重新打开。`tests/test_stale_read_reconciliation.py:489` 新增 run 用例再次直接比较 `iterdir()` 顺序；合法反向枚举实测失败 | 该断言改用已有 `_copy_names()` 或集合比较；反向枚举回归须覆盖新增 run 用例，而不只覆盖旧 helper。正常、反向枚举得到同样的恢复文件内容与通过结果；保持生产创建顺序不变 |
| V01 / P1 条件性风险 | 恢复副本保留子项通过；公开对账接口的结论强度仍有本地缺口。`release_reconciliation.py:45、186–194` 在陈旧视图下输出 `not_committed / production_changed=false / requires_manual_action=false`，application/CLI 原样输出 | 明确区分观察到旧值与证实未提交：对写结果不明、缺乏新鲜度证据的查询输出不确定或需复核信号；同步 CLI/API 与发布文档，消除“确定未提交”与“需另查新鲜度”的矛盾。保留明确拒绝、已提交的独立语义，并评估原 API 消费者兼容性 |

V01 验收应通过公开 application/CLI 验证：fake 远端已是候选而 production 读旧值、pending intent 不可见时，不向调用者给出无需复核的确定未提交结论；生命周期仍保留精确恢复副本；视图收敛及权威明确拒绝分别有正确结果。真实 REST 的可达性/传播延迟继续作为独立证据缺口，不能以等待生产验证替代本地接口语义决策。

本轮只做复审与记录，未修改实现和测试。新增独立复跑 1605 项通过不覆盖未加到正常测试运行中的反向枚举反例；具体证据见 EXECUTION 的 2026-10-17 窗口。

**实施状态（2026-10-17 实施窗口，改动在工作区、未提交未推送；逐项证据见 [EXECUTION 实施窗口](EXECUTION.md#2026-10-17r12--v01-实施窗口)）：**

| 项目 | 状态 | 实施结果与定向回归 |
| --- | --- | --- |
| R12 / P2 | **关闭** | run 用例第 489 行改为 `_copy_names()` 排序比较；反向枚举回归重写为覆盖三条轨迹——生命周期 helper、再次生命周期、以及经真实 `run()` 的恢复目录，注入点在 `run()` 之前。反例变体（把 run 断言换回未排序 `iterdir()` 比较）实测 1 failed（`'previous.yaml' != 'candidate.yaml'`）；生产端未改任何文件创建顺序 |
| V01 / P1 条件性风险 | **本地接口闭环；真实服务证据仍独立开放** | `ReconciliationStatus` 新增 `indeterminate`：旧值 + 无可见 pending intent + 无新鲜度证据时输出 `indeterminate / requires_manual_action=true / observation_freshness="unsupported"`，不再给出无需复核的确定未提交。`reconcile_production_release` / `reconcile_production_release_ids` 新增 `fresh_read_evidence` 参数，CLI 新增 `--observed-after-attempt`；有日期证据时同一状态为 `not_committed / observation_freshness="fresh"`。已提交、明确拒绝与收敛路径语义保持独立并各有回归；反例变体（状态改回 `NOT_COMMITTED`）实测 6 failed |

§0.9 实施完成门在同一工作区内容上重跑，9 行全部退出码 0：1609 passed、50 deselected（77.34 秒，含 coverage 插桩）、覆盖率 84%（floor 75%）、24 个高风险模块全部达标、审计仓库检查 475 文件、doctor `passed`、供应链审计 `passed`。用例数由上一窗口的 1605 增至 1609（+4：V01 公开接口与 CLI 回归）。仍未执行：50 项 integration、远程 CI、真实 Cloudflare / KV、跨平台门与生产故障注入。

### 0.1c 第三轮复审：V01 单字段剩余项（2026-10-17）

R12 在真实 run 路径的反向枚举已通过，本地关闭。V01 新 `indeterminate` 状态、人工复核标记、CLI/application 参数传递与恢复保留均有效；前节“本地接口闭环”的实施判断校正为“仅剩结果字段一致性”。

**P2：** `src/clash_relay/release_reconciliation.py:217` 在无新鲜度证据的 `INDETERMINATE` 分支仍输出 `production_changed=False`。真实状态机 fake 已接受候选，而旧读视图使公开 JSON 同时出现“不确定”与“生产未改变”。该字段已有 `bool | str` 三态设计，未知路径使用 `"unknown"`，无需新增 API。

后续改动限于该分支与对应回归：无新鲜度证据的旧读结果使用 `production_changed="unknown"`；显式新鲜度断言后证实 `not_committed` 仍为 false，已提交和候选原本已在线的结果保持既有规则。同步 `tests/test_release_reconciliation.py`、`tests/test_stale_read_reconciliation.py`、`tests/test_production_application_unit.py` 中把 indeterminate 锁定为 false 的断言；通过公开 application/CLI 核对 JSON 字段组合。保留反例中的“实际存储已是候选”独立断言，避免仅复述实现。

验收：首次/后续发布的陈旧视图均同时输出 indeterminate、unknown、requires_manual_action=true；新鲜度证据路径、committed、run 恢复保留与反向枚举相关回归通过。本轮未发现其他重要回归；真实服务与远程 CI 验证仍单列，不把该字段缺陷描述为已发生生产故障。

**实施状态（2026-10-17 第三轮实施，改动在工作区、未提交未推送；逐项证据见 [EXECUTION 第三轮实施窗口](EXECUTION.md#2026-10-17第三轮实施窗口-v01-字段一致性)）：**

| 项目 | 状态 | 实施结果与定向回归 |
| --- | --- | --- |
| V01 P2 结果字段一致性 | **关闭（本地）** | `release_reconciliation.py:217` 的无新鲜度证据分支改用 `production_changed="unknown"`；有日期证据的 `not_committed=False`、`committed`、候选原本已在线的 `False` 及 `unknown` 分支不变。反例变体（还原为 `False`）实测 6 failed / 1 passed，通过项正是日期证据用例；恢复后源文件与改动前逐字节相同（sha256 `d9a8bd23c365…`）。三份测试同步把不确定断言改为 `"unknown"` 并保留“实际存储已是候选”的独立断言；公开 `reconcile_production_release` 探针显示首发与后续陈旧视图均为 `indeterminate / "unknown" / requires_manual_action=true`，日期证据路径为 `not_committed / false`，已证实提交仍为 `committed / true`；真实 `run()` 反向枚举轨迹 passed |

§0.9 实施完成门在同一工作区内容上重跑，9 行全部退出码 0：1609 passed、50 deselected（77.04 秒）、覆盖率 84.32%（floor 75%）、24 个高风险模块全部达标（`release_reconciliation.py` 92.65% / 门 90%）、审计仓库检查 475 文件、doctor `passed`、供应链审计 `passed`。用例数与覆盖率与上一窗口相同，未删除或跳过用例。文档定稿后重跑同一脚本（run B）仍为 9 行退出码 0（135.56 秒）；随后把三个与 `.gitattributes`（`* text=auto eol=lf`）不符的 CRLF 文件按字节规范化为 LF，并在最终树重跑第三次（run C，`gate-run-c1-final2.txt`）仍为 9 行退出码 0、1609 passed、覆盖率 84.32%（134.30 秒）。仍未执行：50 项 integration、远程 CI、真实 Cloudflare / KV、跨平台门与生产故障注入。

**2026-10-17 独立复审确认：§0.1c 本地剩余项已关闭。** 原陈旧视图反例现输出 `indeterminate / production_changed="unknown" / requires_manual_action=true`；R12 run 反向枚举通过。92 项相关回归与静态/离线审计通过，未新增整改事项。真实服务证据与远程 CI 继续独立记录，不将本地 PASS 扩大为生产验收。

**2026-10-17 推送与远程 CI：本批已提交（`a3b718b`）并推送；首次推送暴露 Windows 门缺陷，修复提交 `32ad94b` 后 run `38952400087` 全部 10 个 job 通过，`validated` SHA = `32ad94b`。** 缺陷根因：`windows-latest` 镜像把自身工具装进 `actions/setup-python` 的同一解释器，`pipx 1.17.12` 要求 `packaging>=26.3` 而哈希锁固定 `25.0`，新增的 `pip check` 因此报告本项目不拥有的冲突。修复让该 job 全程使用 `.work/dev-venv`，判定范围限定为自有环境。O6 的报告留存由该 run 的真实 artifact 验证：6 个 artifact 全部非空，quality 归档仅含两个白名单 XML。`deploy` 按 `github.event_name != 'push'` 跳过，生产未触碰。详见 [EXECUTION](EXECUTION.md#2026-10-17推送远程-ci-与-windows-门修复)。

### 0.2 R07：脱敏函数应处理畸形 URL

- **证据与影响：** `src/clash_relay/redact.py:18–32` 仅捕获 `urlsplit()` 的异常，`parsed.port` 的延迟校验位于保护外。`redact_text("error https://fixture.invalid:bad/private")` 和端口 `70000` 均抛 `ValueError`。`mihomo.py:113、135` 在组装 `ValidationError` 前调用它，因此异常诊断可能再次失败。未据此断言已有秘密泄漏。
- **改动：** 将 hostname/port 的提取也纳入畸形输入处理；解析失败返回固定脱敏占位值；保持正常 IPv4/IPv6 origin 的诊断能力。
- **验收：** 非数字/越界端口、损坏 IPv6、userinfo、查询参数的回归通过；模拟 Mihomo 失败输出仍抛项目规定异常，输出不包含合成秘密、路径或参数。入口：`tests/test_status_and_security.py`、`tests/test_mihomo_unit.py`。

### 0.3 R08：在任何发布 I/O 前校验全部派生键

- **证据与影响：** `schemas/config.schema.json:194` 允许 512 字符，`publishers/cloudflare_kv.py:73–79` 限制 512 UTF-8 字节；`release_bundle.py:45–51` 的 manifest 后缀占 85 字节，基键实际最多 427 字节。实测 427 字节基键的 manifest 为 512 字节并通过，428 字节则为 513 字节并报错。`release_bundle.py:168–177` 先处理 config 再构造 manifest publisher；428/429 字节基键可能先写入合法 config 对象，随后才失败。此写入次序为代码证据，未向真实 KV 执行。
- **改动：** 建立统一的派生键验证，在配置语义检查/doctor 与发布入口共用；覆盖 release config/manifest、指针、journal、transaction 及辅助状态键。按 UTF-8 字节验证，保留低层 publisher 自校验。
- **验收：** 427/428 字节、中文多字节基键、全部后缀组合；非法输入在 fake publisher 首次读写前失败；合法默认键和已有发布/恢复契约通过。不要只把 JSON Schema 的字符上限改成 427。

### 0.4 R09：供应链审计按 YAML 结构识别引用

- **证据与影响：** `scripts/audit_supply_chain.py:49–57` 只扫描 `*.yml` 和以 `- uses:` 开头的文本。命名 step 中独立一行的 `uses: actions/checkout@main` 在合成工作流中通过审计。仓库 `.github/workflows/branch-housekeeping.yml:34–35` 已使用该格式，其当前引用是完整 SHA；本问题是审计缺口，并非发现当前浮动引用。job 级外部 reusable workflow 与 `.yaml` 同样不在扫描逻辑内。
- **改动：** 解析两种扩展名的工作流，遍历 `jobs[*].uses` 和 `jobs[*].steps[*].uses`；区分本地引用、外部 action/workflow，明确其他引用形式的固定规则。解析失败应令审计失败。
- **验收：** 普通/命名 step、job reusable workflow、`.yml/.yaml` 中的 `@main` 与 tag 均失败；完整 SHA、本地 workflow 通过；换行、注释、字段顺序不影响结果。使用临时工作流负向 fixture，避免只断言源码字符串。

### 0.5 R10：拒绝含重复键的声明

- **证据与影响：** `src/clash_relay/util.py:63–76` 拒绝锚点和别名后仍调用默认 `yaml.safe_load()`。合成输入 `enabled: false\nenabled: true\n` 返回 `{'enabled': True}`，丢失首个声明，后续 Schema 只看到覆盖后的值。人工审阅与实际开关、路由或权限声明可能出现偏差；没有发现当前跟踪配置本身存在重复键。
- **改动：** 在共享 YAML loader 的映射构造边界检测重复键，提供安全的位置诊断；保留现有 NUL/锚点/别名异常分类。公共配置应严格拒绝；订阅侧明确文档级拒绝与 `on_error` 行为，不悄悄选择某个重复值。
- **验收：** 顶层/嵌套重复键、相同值重复、不同映射中的同名键、正常配置；必需源失败与可选源跳过均保持既有契约；错误信息不输出凭据值。明确这是收紧含歧义输入的兼容行为。

### 0.6 R11：覆盖率数据必须为有效比例

- **证据与影响：** `scripts/audit_risk_coverage.py:65、93` 接受 `float("NaN")`，阈值比较不产生失败。合成 XML 为全部 24 个风险模块填入 `line-rate="NaN"`，实际审计返回 0 个 failures。未发现正常 pytest-cov 生成这种报告，也不将其表述为现有覆盖率虚高。
- **改动与验收：** 解析时要求有限数且位于 `[0, 1]`；NaN、正负 Inf、负数、大于 1 的值和缺失报告均失败；合法边界、等于 floor 与低于 floor 的行为保持正确。

### 0.7 V01：确认陈旧观察与“确定未提交”的边界

- **已知代码事实：** `release_reconciliation.py:182–194` 在读到旧 production、非候选 current、无可见 transaction 时返回 `not_committed`；`:45` 将其视为无需人工处理。内存旧视图已复现该结果。`production_lifecycle.py:614` 对该结论跳过本地恢复副本保留，`:838` 最终清理 private 目录；入口是 `CommitUnknownError` 处理（`:801–820`）。这不等于删除 KV 中的持久事务或 immutable 对象。
- **待确认假设：** 实际 Cloudflare REST API 在一次已接受写入之后，是否能在该调用链产生这种完整旧视图，以及 pending transaction 的写入/读回顺序是否足以排除它。本轮没有真实服务证据。`docs/publishing.md:143` 已明确单次读取不是原子快照，需对齐此处的结论强度。
- **调查及验收：** 建立“实际存储状态”与“每次读视图”分离的 fake，覆盖写入生效但响应丢失、transaction 暂不可见、读视图收敛、明确拒绝和首次发布。通过生命周期验证恢复文件是否保留，不能只测 helper 返回值。如该状态可达，未获确定未提交证据时保留 `unknown`，不得触发自动补偿/重放；收敛后应正确判定 committed。记录 API 契约/后续服务验证证据，再关闭风险；若证实不可达，也记录具体顺序约束与回归。

### 0.8 可优化项

| 编号 | 证据 / 收益 | 方案与验收 |
| --- | --- | --- |
| O4 | 本轮非集成测试 100.41 秒；最慢 10 项发布失败测试合计约 52.5 秒，呈 7.50/3.75 秒固定等待；`release_bundle.py:27、106–111` 使用真实 sleep | 在纯状态机测试中注入/替换等待函数并记录延迟序列，保留生产退避配置、重试次数和停止条件断言；保留少量计时边界测试。运行相关子集与一次全套 durations，比较耗时和判别力，不以删重试语义换速度 |
| O5 | `audit_supply_chain.py:29` 只检查锁条目版本/哈希格式；CI 使用 `--no-deps` 安装，缺少 `pip check`。当前直接依赖版本未见冲突 | 比较规范化依赖元数据与锁版本；开发/独立 wheel 环境安装后执行 `pip check`；开发缓存 key 同时包含两个锁文件。仅修改一侧版本的 fixture 应失败，运行锁变更应改变缓存 key；不因此扩大依赖升级范围 |
| O6 | `.github/workflows/ci.yml` 生成 JUnit/coverage XML，但无报告上传；ACL4SSR 多次尝试共用报告路径 | 仅上传合成测试报告白名单，失败时也保留，以 Python/core/attempt 区分并设短保留期；测试失败和多次重试报告均可定位。生产配置、订阅与整个 `.work` 均不在报告归档范围 |

### 0.9 实施完成门

1. R07–R11 各有能在修复前失败、修复后通过的定向反例；行为调整同步相关文档。
2. V01 有状态机可达性结论、生命周期证据及依据；待验证时维持独立状态，不混写为已修复。
3. 在共同代码输入上执行一次 lint/格式、canonical typecheck、非集成测试及覆盖率/风险覆盖率、离线审计、公共 doctor；跨平台与真实内核按实际改动选取现有 CI 门。
4. O4–O6 各自提供耗时、依赖冲突反例或报告留存证据；可独立交付，不阻塞无关缺陷修复。
5. 记录实际命令、退出码、适用范围与未执行项；本地完成与远程 CI/真实服务结果分别记录。

**实施完成门的逐项状态（2026-10-16 实施窗口）：** 门 1 满足（各项反例见 §0.2–§0.6 与 EXECUTION 的批量负向对照：源码回退后 54 failed / 22 errors）；门 2 满足证据要求但 V01 **保持待验证**，不并入已关闭项；门 3 满足（lint / 格式 / 58 文件类型门 / 1567 passed + 覆盖率 84.21% / 风险覆盖率 / 离线审计 / 公共 doctor 全部 exit 0）；门 4 满足（O4 耗时、O5 依赖漂移反例与真实 `colorama` 缺口、O6 归档契约）；门 5 满足（命令、退出码、范围与未执行项记录在 EXECUTION，本地与远程分开）。跨平台与真实内核门（3.11 / 3.12 / Windows / stable-mihomo / arm64）本轮未派发，待推送后按现有 CI 记录。

---

以下为已完成的 R01–R06 / O1–O3 历史实施方案。

## 1. 目标、范围与依赖

目标：关闭检查发现的六项缺陷，补齐直接对应的失败路径回归，在现有发布、来源隔离与协议语义契约下完成可复核的软件交付。

**T0 基线 → T1 工作流权限 / T2 发布恢复 / T3 订阅解析 / T4 平台门禁 → T5 整体验收。** T1 优先；T1–T4 无业务先后依赖，可独立推进。T2 内两项共享发布失败语义，联合验证。并行实现若涉及同一文件，由一个责任方统一整合；整体验收使用稳定的共同输入。

保持现有 Public Config v2、订阅来源权限、六个主要场景、私有 Cloudflare 发布路径和确定性生成。没有实际证据支持的性能重构、全仓类型改造和新功能不进入本轮必选范围。

本次文档生成不包含 push、merge、远程 workflow_dispatch 或生产写入。后续实施先完成本地可审查结果；真实外部动作按当时明确授权执行。远程验证待执行不阻止独立本地整改。

| 编号 | 问题 | 优先级 | 阶段 | 主要代码入口 |
| --- | --- | --- | --- | --- |
| R01 | 复用 CI 的调用方权限缺口 | P1 | T1 | `.github/workflows/rollback.yml`、`retention.yml`、`ci.yml` |
| R02 | finalize 中断后的相同候选重试阶段倒退 | P2 | T2 | `release_bundle.py`、`release_transaction.py` |
| R03 | VMess 数值 / JSON 异常逃逸 | P2 | T3 | `uri_parser.py`、`subscription_parser.py`、`builder.py` |
| R04 | VMess gRPC serviceName 丢失 | P2 | T3 | `uri_parser.py`、协议保真与内核集成测试 |
| R05 | Cloudflare 畸形成功响应误判为明确拒绝 | P2 | T2 | `publishers/cloudflare_kv.py`、`release_bundle.py` |
| R06 | Windows 类型门禁报 POSIX API 错误 | P3 | T4 | `mihomo_probe_runtime.py`、类型检查入口与 CI |

## 2. T0：固定共同基线与复现证据

1. 实施前记录 HEAD、相关 diff、Python / Ruff / mypy 版本、操作系统及当前 Mihomo manifest。HEAD 只标识提交；有本地修改时同时保留实际输入差异或文件 hash。
2. 承接 EXECUTION 中已经执行的 1405 项测试和静态检查结果。它们是修复前基线，不写成修复后验证，也不把子集测试数量累加为额外覆盖。
3. 把 R02–R05 的最小反例固化为本地合成测试。R03 至少有“异常条目 + 正常条目”和可选源构建边界；R05 至少有“写入已生效但响应异常”的状态模型。
4. R01 先核对所有本地 reusable workflow 调用链的有效权限；R06 保存当前平台真实错误。此前 Mock / 内存复现不构成真实 KV 或网络证据。
5. 新执行窗口使用独立 `.work/acceptance-<timestamp>/` 保存命令、stdout/stderr、退出码和环境摘要。仅保存合成输入及聚合结果，不把真实订阅或候选凭据写入文档。

**完成条件：** 六项问题均有精确入口和判别性反例或静态证据；实施输入可追溯；已执行与待执行项目分开。没有源码变化时不为建档重复全套测试。

## 3. T1：修复回滚与清理工作流权限（R01）

### 改动范围

- 让 rollback、retention 调用 CI 时的有效权限满足被调用工作流需求。优先沿用 publish 已有的 `actions: read` + `contents: read` 最小组合；若收缩 CI 权限到具体 job，应检查所有调用者和 validation reuse 路径。
- 不顺带开放写权限，不调整 confirm / main 限制、validated SHA 绑定或生产提交并发锁。
- 在现有治理、恢复与 retention 测试附近增加调用方与被调用方权限兼容校验，检查有效权限语义，而非只断言字符串出现。

### 验收场景

| 场景 | 预期 |
| --- | --- |
| rollback / retention 调用 CI | 请求权限均包含在调用方许可内 |
| 移除 caller 的 actions: read 的负向 fixture | 检查明确失败，定位 caller 与缺失权限 |
| publish、release 等其他本地调用链 | 既有校验与复用路径保持有效 |
| confirm=false、非 main、validated SHA 不匹配 | 现有业务执行限制保持成立 |

**完成条件：** 本地权限兼容回归与相关 workflow 契约测试通过；真实 GitHub 运行若未执行，保留 NOT_EXERCISED，不用本地 YAML 解析冒充平台校验。

## 4. T2：发布恢复与未知提交状态（R02、R05）

### 4.1 R02：阶段单调、幂等恢复

- 根据实际事务 phase 恢复，避免将 finalize 回退到 verify。已经 finalize 的事务仍核对 production、current / previous pointers 和所需 immutable 对象；状态匹配后完成清理。
- 保留正常事务的阶段单调约束。不要通过允许任意阶段倒退来消除错误。
- 清理失败、响应丢失与短期旧读仍保留待恢复状态；恢复不重复覆盖已确认的生产字节，也不跳过 predecessor 完整性验证。

必须覆盖：首次发布与已有前序版本、正常提交、finalize 写入后进程中断、清理明确失败、清理结果不明、相同候选重试、不同候选路径、pointer 矛盾、immutable 缺失。断言最终 KV 内容、事务状态、指针和实际写入顺序，不能只断言函数返回成功。

### 4.2 R05：区分成功、明确拒绝与未知结果

- 按接口契约接受有效成功响应、识别明确拒绝。缺少 success、null、错误类型及不完整响应不得直接当作明确拒绝。
- 对写请求保留 CommitUnknownError 语义，并核对读请求、重试、辅助状态写入的既有行为。
- 测试“生产 PUT 已接受 → 返回异常响应 → read-back 仍为旧值”：上层停止进一步补偿写，留下可核对的事务与未知状态。
- 明确拒绝仍走既有失败 / 补偿策略；正常响应和已有超时、无效 JSON 分支保持行为一致。

### 4.3 联合完成门

定向入口：`tests/test_release_transaction*.py`、`test_release_bundle*.py`、`test_release_reconciliation.py`、`test_cloudflare_kv*.py` 及控制面失败矩阵。实际命令列出明确文件，跨 shell 不依赖通配符自动展开。

**完成条件：** 相同候选在 finalize 中断后可完成幂等恢复，清理后 previous release 可正常解析；未知提交结果不触发盲目补偿或重放；明确拒绝、正常发布、首次发布与回滚前置契约均保持有效。Mock 覆盖的是调用与状态机逻辑，不宣称验证了 Cloudflare 一致性时延。

## 5. T3：订阅容错与协议保真（R03、R04）

### 5.1 R03：在解析边界归一化失败

- VMess JSON 解码、端口和 alterId 转换显式处理非有限数值、错误类型、越界及过深结构，转换为项目订阅解析异常。
- 深度保护覆盖解码过程抛出的 RecursionError，而不只在 json.loads 返回后检查。
- 尊重 invalid_proxy_policy 与 subscription on_error / required 策略；不以捕获所有 Exception 掩盖内部程序错误。

必须覆盖：`1e999`、深嵌套 JSON、正常端口、畸形字段；skip 模式下保留同列表正常节点；fail 模式下明确失败；可选源失败不拖垮其他有效源；必需源和 minimum_usable_nodes 等门禁继续生效。用 builder 层回归证明异常边界有效，避免只测 helper。

### 5.2 R04：VMess gRPC 参数完整映射

- 根据受支持的 VMess 分享格式确认 `net=grpc` 与 `path` 的 serviceName 语义，映射到 Mihomo `grpc-opts.grpc-service-name`。
- 检查同一分支的 TLS、SNI、ALPN 及空 serviceName 语义；只纳入有契约依据的字段，不猜测新协议扩展。
- 不支持的显式参数组合应在解析阶段给出明确结果，避免接收后生成语义变化的节点。

必须覆盖：非空与空 serviceName、TLS / SNI 组合、既有 TCP / WebSocket 节点不退化、URI → 标准代理 → 生成 YAML 的字段一致性。为非空 serviceName 建立本地 gRPC fixture，并让真实 Mihomo 发起请求验证正确路由；仅 `-t` 成功不足以证明服务名生效。若环境未具备运行条件，记录为未执行并保留本轮验收缺口。

**完成条件：** 两类最小反例均由回归锁定，正常 URI 的协议语义保持一致，真实 gRPC 链路取得证据后关闭完整协议验收门。

## 6. T4：跨平台静态检查与进程回归（R06）

- 使用类型检查器可识别的平台分支或等效明确边界，消除 Windows 的 killpg / SIGKILL 报错。
- 保持 Windows terminate / kill 与 POSIX process-group TERM / KILL 行为；不通过全局 ignore、删除类型目标或禁用错误码使门禁变绿。
- Windows 上执行现有 canonical typecheck 和 Mihomo process 定向单测；Linux 验证 POSIX 分支及原有完整类型门禁。
- CI 增加 Windows 定向静态 / 进程检查，依赖沿用锁文件；避免复制现有整个 Python × 内核矩阵。新增 job 若作为必需门，同步 validated 聚合条件及治理测试。
- 实施修正（2026-10-14）：原锁文件只覆盖 Linux x64 runner，Windows 无法复用。改为在同一对锁文件中补 Windows x64（CPython 3.12）wheel 哈希，锁头声明两个支持平台，不新增锁文件。Windows runner 的默认 shell 是 PowerShell，该 job 的命令须用 PowerShell 语法且每条命令独立成步。

**完成条件：** Windows 3 个已知错误消失，Linux 与 Windows 对应进程回归通过；类型门禁继续有判别力，CI 新门的失败会阻止 validated 成功。

## 7. T5：整合验证与交付

先完成各阶段定向检查，再在共同输入上执行一次完整检查。以下命令均从仓库根目录执行；本表是待执行入口，不是运行记录。

| 验证 | 命令 / 入口 | 完成要求 |
| --- | --- | --- |
| Lint / 格式 | `python -m ruff check .`；`python -m ruff format --check .` | 各命令 exit 0 |
| 类型 | `python scripts/run_project_checks.py typecheck` | 本轮目标平台通过 |
| 单测与覆盖率 | `python -m pytest -m "not integration" --cov=clash_relay --cov-fail-under=75 --cov-report=term-missing --cov-report=xml:.work/acceptance-<timestamp>/coverage.xml --junitxml=.work/acceptance-<timestamp>/unit.xml` | 全部通过，新增反例实际执行；先创建实际证据目录并替换占位符 |
| 高风险覆盖率 | `python scripts/audit_risk_coverage.py .work/acceptance-<timestamp>/coverage.xml` | 使用同一轮 coverage，既有 floor 不降低 |
| 审计 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | 所有子门通过；离线结果不等于已核对实时上游 |
| 公共配置 | `python -m clash_relay.cli doctor --public-only` | passed |
| Python 兼容性 | 沿用 CI 的 3.11 / 3.12 / 3.13 矩阵 | 修复后源码均通过 |
| 生成与安装 | 沿用 CI 的双次 fixture 生成字节比较、wheel 构建与源码树外 smoke | 确定性一致、安装后入口可用 |
| 真实内核 | 沿用 stable-mihomo job；版本只读取 `tools/mihomo-versions.json` | stable 核心矩阵通过，并纳入 R04 gRPC 实际传输回归 |

执行多个原生命令时分别保存退出码；PowerShell 最后一个命令成功不代表之前的命令通过。已通过检查只在输入变化、新失败或剩余疑点需要时重跑。

**软件完成条件：** R01–R06 的本地实现及对应反例关闭，全部适用门通过，没有新增严重回归，差异只含相关修复 / 测试 / 文档。真实内核未执行时交付阶段报告，不宣布完整协议验收通过。

**外部验证边界：** GitHub 工作流校验、真实 Cloudflare dry-run、发布、回滚与 retention 单列状态；生产写入不纳入本地软件验收。后续若开展真实流程，绑定精确 validated SHA，沿用既有发布契约，不为演示本计划而执行删除或回滚。

## 8. O1–O3：持续优化与取舍

| 项目 | 安排 | 验收方式 |
| --- | --- | --- |
| O1：用失败路径补足测试判别力 | 本轮必做，随 T1–T3 | 权限缺口、阶段中断、未知响应和异常 URI 的反例在修复前失败、修复后通过，并有正常正对照 |
| O2：平台门禁覆盖实际开发环境 | 本轮随 T4 | Windows 定向检查进入 CI，Linux 核心检查保持；矩阵扩展成本可说明 |
| O3：分批加强高风险类型边界 | 后续独立批次，不阻塞六项修复 | 先评估 URI / 订阅 / 发布边界的未覆盖模块及导入；每批记录新增检查范围与实际发现，不一次打开全仓 strict，也不以 Any 或全局 ignore 换取通过 |

当前类型门禁列出 49 个目标，源码共 116 个 Python 模块；这只是范围差异，不直接证明每个未覆盖模块存在缺陷。暂不依据文件长度拆模块，也不在缺少基准时承诺性能收益。

## 9. 执行记录与交接规则

每个窗口向 EXECUTION 追加：日期 / 阶段、源码输入、环境、准确命令及退出码、证据路径、PASS / FAIL / NOT_EXERCISED / INCONCLUSIVE、已知边界和下一动作。README 同步当前门状态，PLAN 只在范围或方案改变时修订。

历史失败与后续修复成功同时保留。现有会话输出缺少持久日志的事实如实标注；新增证据只证明本次新运行，不补造旧日志。完整软件验收、远程平台验证、生产发布就绪分开判断。
