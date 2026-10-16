# clash-relay 执行与证据记录

当前状态见 [README](README.md)，唯一后续计划见 [PLAN](PLAN.md)。本文件按实际窗口追加；计划中的命令不记成已执行，修复前测试不记成修复后结果。

## 2026-10-13：项目检查基线（承接本次会话已执行结果）

范围：只读审查关键实现、配置与工作流，执行本地非集成测试、静态检查、离线审计和合成复现。未修改业务代码，未访问生产订阅或 Cloudflare，未触发远程工作流。

### 输入与证据边界

- 本轮计划编制时 HEAD：`95cad44692b4e8ec890bd68fc2163dbc4b14fb6a`；Git 状态只有用户提供的未跟踪 `docs/acceptance/`，未见业务代码差异。
- 已执行检查环境：Windows PowerShell，Python 3.13.15、pytest 9.0.2、Ruff 0.14.10、mypy 1.18.2。
- 下表承接同一会话的真实工具输出。本次未找到或建立该次完整持久日志归档，因此不附造出的日志路径；后续实施按 T0 重新固定所需反例与证据。当前 HEAD 记录不是一份旧测试输入的逐文件 hash manifest。

### 已执行的检查

| 命令 / 范围 | 实际结果 | 适用边界 |
| --- | --- | --- |
| `python -m pytest -m 'not integration' -q --tb=short` | exit 0；1405 passed、46 deselected；81.31 秒 | 修复前非集成基线；未启用本轮 coverage |
| `python -m ruff check .` | All checks passed | 源码静态风格检查 |
| `python -m ruff format --check .` | 349 files already formatted | 格式检查 |
| `python scripts/run_project_checks.py typecheck`（独立重跑确认退出码） | exit 1；49 个目标中 1 个文件报 3 个错误 | Windows 平台的 killpg 两处、SIGKILL 一处；不推定 Linux 也失败 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；文档、架构、SLO、客户端/DNS、service qualification、v2 release、供应链、仓库安全审计通过 | ACL4SSR 离线比对 matched_with_declared_deviations，upstream_reference_verified=false |
| `python -m clash_relay.cli doctor --public-only` | exit 0；status=passed | 订阅 Secret、Cloudflare 和 release state 检查均 skipped |
| 发布审查定向子集 | 46 passed | 与全套测试重叠，不累加到 1405 |
| 订阅审查定向子集 | 95 passed | 与全套测试重叠，不累加到 1405 |

部分检查最初在同一 PowerShell 命令中顺序执行；typecheck 的失败通过独立命令确认。后续门禁记录各命令退出码，避免最后一条命令掩盖前序失败。

### 已确认的问题与复现

| 编号 | 证据 | 当前结论 |
| --- | --- | --- |
| R01 | rollback / retention caller 仅 contents: read，ci.yml 声明 actions: read + contents: read | 静态权限不兼容；未以实际 GitHub 运行验证 |
| R02 | 内存 KV：先发布 old，再发布 new；在 transaction 清空写入处中断；重试 new | 保存 phase=finalize；重试抛 PublicationError（phase regression）；previous release 读取被 unfinished transaction 拒绝 |
| R03 | VMess JSON port=1e999；另有深嵌套 JSON 合成输入 | skip 模式仍抛 OverflowError；深结构产生未归一化 RecursionError |
| R04 | VMess net=grpc、path=fixture-service、tls=tls | 标准代理为 network=grpc，但 grpc-opts=None；未执行真实 gRPC 传输 |
| R05 | Mock HTTP 成功响应为 b'{}'，调用固定 namespace 的 publisher | 返回普通 PublicationError；未知提交状态未保留。对真实 KV 的影响尚属状态机分析，不声称已在生产发生 |
| R06 | Windows canonical typecheck | mihomo_probe_runtime.py 的 killpg / SIGKILL 属性错误共 3 项 |

R03 / R04 的可重放合成输入（用于后续固化回归，不含真实凭据）：

```python
import base64
import json
from clash_relay.subscription_parser import parse_subscription

bad = "vmess://" + base64.b64encode(b'{"port":1e999}').decode()
# 当前实际结果：OverflowError；期望按 skip / fail 契约处理。
parse_subscription(bad, invalid_policy="skip")
```

```python
import base64
import json
from clash_relay.subscription_parser import parse_subscription

data = {
    "v": "2", "ps": "fixture", "add": "node.invalid.example",
    "port": "443", "id": "00000000-0000-4000-8000-000000000001",
    "aid": "0", "net": "grpc", "path": "fixture-service", "tls": "tls",
}
uri = "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()
proxy = parse_subscription(uri).proxies[0]
print(proxy["network"], proxy.get("grpc-opts"))
# 当前实际输出：grpc None。
```

R02 / R05 由审查子任务通过内存 KV / urllib Mock 验证，最小测试尚未持久化；T0 将其落成可重复执行的测试并保存新运行日志。旧全套测试通过与新反例失败同时成立。

### 未执行范围

真实 Mihomo 集成矩阵、gRPC 端到端传输、Python 3.11 / 3.12 本轮运行、修复后 coverage、wheel 构建、GitHub 远程工作流和生产生命周期均未执行。这里没有修复完成或发布就绪结论。

## 2026-10-13：按 acceptance 样例建立项目计划（文档窗口）

范围：读取用户提供的三个未跟踪样例，沿用 README / PLAN / EXECUTION 职责，替换为 clash-relay 当前状态、后续步骤和本次检查证据。未实施 R01–R06。

### 原样例保全

原样例位于 `docs/acceptance/`，内容属于其他项目。重写前逐文件复制到 `.work/acceptance-template-20261013-192922/`，比较 SHA-256 全部一致；备份清单为该目录的 `manifest.json`。该目录已被项目 `.gitignore` 排除，仅作为本地保全副本。

| 文件 | 原样例 SHA-256 |
| --- | --- |
| README.md | `2AFFE28055E5CE3543D81FBFE533A0FB2157AD5AF6E5B263B0169E7CFB371846` |
| PLAN.md | `025EE84C136CEFEF93439C265927C82906B3A11F4457432B4C5CE2A6B3B6EACD` |
| EXECUTION.md | `1AC2018E9D32C71ACAF5E93FA8986F5242D7B935AF52E309B748C9E2459DEDA0` |

### 交付内容

- README：六项问题与 T0–T5 当前状态、下一动作、权威规范导航。
- PLAN：依赖、改动边界、正反例、逐阶段完成门与可执行验证入口；O1–O3 明确本轮必做与后续分批范围。
- EXECUTION：只承接实际检查与本次文档行为，保留证据缺口。

### 文档验证（本窗口实际执行）

- `python scripts/audit_documentation_contract.py`：exit 0，documentation contract: passed。
- 本地文档检查：3 份文件、13 个本地链接、失效链接 0；Markdown 代码围栏成对；六个问题编号与六个阶段齐全；未残留样例项目的专属验收条目。
- 原样例 3 份备份按 manifest 再次核对 SHA-256，全部一致；`git check-ignore` 确认备份目录被忽略。
- `git status --short` 仍仅显示 `?? docs/acceptance/`；业务源码、测试和工作流未修改。因仅修改文档，本窗口未重跑业务测试。

### 下一窗口记录模板

执行后再追加记录，至少包含：日期 / 阶段；HEAD 与实际差异；环境；准确命令和逐项退出码；日志或测试报告路径；结果及未覆盖范围；是否有外部状态变更；下一动作。失败后修复通过追加新结果，原失败原位保留。

## 2026-10-14：实施 R01–R06 与本地整体验收（实施窗口）

范围：在工作区落地 R01–R06 的源码修复、对应失败路径回归与文档，并执行 T5 本地门。未触发远程工作流，未访问生产订阅或 Cloudflare，未执行发布 / 回滚 / retention 或任何生产写入。

### 输入与环境

- HEAD：`95cad44692b4e8ec890bd68fc2163dbc4b14fb6a`；本轮改动在此基线之上，尚未提交。
- 工作区输入清单（含逐文件 sha256、`git diff --stat`）：`.work/acceptance-20261014-003658/input-manifest.txt`。
- 环境：Windows（MINGW64_NT-10.0-26100）、Python 3.13.15、pytest 9.0.2、Ruff 0.14.10、mypy 1.18.2。见同目录 `env.txt`。
- 证据目录：`.work/acceptance-20261014-003658/`（`.work` 已被 `.gitignore` 排除，仅本地留存）。

### 源码修复

| 编号 | 入口 | 改动 |
| --- | --- | --- |
| R01 | `.github/workflows/rollback.yml`、`retention.yml` | 调用 CI 的 job 增加 `actions: read`，与 ci.yml 声明的 `actions: read` + `contents: read` 对齐；未开放写权限，未改动 confirm / main / validated SHA 限制。 |
| R02 | `src/clash_relay/release_bundle.py`、`release_transaction.py` | 新增 `phase_reached()`；恢复按持久 phase 续跑，finalize 中断不再回退到 verify。 |
| R03 | `src/clash_relay/uri_parser.py` | 新增 `_coerce_vmess_integer()`、`_decode_vmess_json()`；非有限数值、错误类型与过深 JSON（含解码期 RecursionError）统一归一为 SubscriptionError。 |
| R04 | `src/clash_relay/uri_parser.py` | `net=grpc` 时把 `path` 映射为 `grpc-opts.grpc-service-name`；空 serviceName 不生成 grpc-opts。 |
| R05 | `src/clash_relay/publishers/cloudflare_kv.py` | 仅 `success is True` 视为成功、`success is False` 视为明确拒绝；缺失 / null / 错误类型在写路径保留 CommitUnknownError，读路径维持明确失败。 |
| R06 | `src/clash_relay/mihomo_probe_runtime.py` | 平台分支改用 `sys.platform == "win32"`，类型检查器可识别，消除 Windows 的 killpg / SIGKILL 报错；未用全局 ignore 或删除类型目标。 |

### 反例（修复前失败 → 修复后通过）

承接上一窗口的合成复现（`.work/acceptance-20261013-193804/repro_*.out` 与 `*.after.out`）：R02 phase regression → `recovered=True`；R03 逃逸 OverflowError / RecursionError → 归一化并按 skip / fail 契约处理；R04 `grpc-opts=None` → `{'grpc-service-name': 'fixture-service'}`；R05 误判为明确拒绝 → CommitUnknownError；R01 `missing=['actions']` → 通过；R06 3 errors → 0 errors。上述反例已固化为本仓库回归（见下表 T1–T3 定向）。

### 本地验收门（本窗口实际执行）

| 验证 | 命令（仓库根目录） | 退出码 | 结果 |
| --- | --- | --- | --- |
| Lint | `python -m ruff check .` | 0 | All checks passed |
| 格式 | `python -m ruff format --check .` | 0 | 353 files already formatted |
| 类型（Windows） | `python scripts/run_project_checks.py typecheck` | 0 | Success: no issues found in 49 source files |
| 类型（Linux 语义） | `mypy --follow-imports=skip --ignore-missing-imports --check-untyped-defs --platform linux <49 目标>` | 0 | Success: no issues found in 49 source files |
| 单测 + 覆盖率 | `pytest -m "not integration" --cov=clash_relay --cov-fail-under=75 --cov-report=term-missing --cov-report=xml:… --junitxml:…` | 0 | 1453 passed, 50 deselected；总覆盖率 84.09%（floor 75%） |
| 高风险覆盖率 | `python scripts/audit_risk_coverage.py <coverage.xml>` | 0 | risk-based coverage ratchet: passed |
| 审计 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | 0 | 文档 / 架构 / SLO / 客户端 DNS / service qualification / v2 release / 供应链 / 仓库安全全部通过；ACL4SSR `matched_with_declared_deviations`，`upstream_reference_verified=false` |
| 公共配置 | `python -m clash_relay.cli doctor --public-only` | 0 | status=passed |
| 真实内核 gRPC（R04） | `MIHOMO_BIN=.work/mihomo-1.19.30.exe pytest tests/integration/test_vmess_grpc_transport_mihomo.py -m integration` | 0 | 2 passed（真实 Mihomo 客户端流随 serviceName 变化） |
| 定向 T1 | `pytest tests/test_workflow_permission_contract.py tests/test_release_transaction.py tests/test_release_transaction_recovery.py tests/test_release_publish_unknown_response.py tests/test_cloudflare_kv_commit_state.py tests/test_vmess_resilience.py tests/test_uri_golden_contract.py tests/test_mihomo_probe_runtime.py -m "not integration"` | 0 | 111 passed |
| 定向 T2 | `pytest tests/test_release_bundle.py tests/test_release_bundle_commit_unknown.py tests/test_release_bundle_failure_boundaries.py tests/test_release_reconciliation.py tests/test_release_transaction.py tests/test_release_transaction_recovery.py tests/test_release_publish_unknown_response.py tests/test_cloudflare_kv.py tests/test_cloudflare_kv_commit_state.py tests/test_cloudflare_control_plane_failure_matrix.py -m "not integration"` | 0 | 136 passed |
| 定向 T3 | `pytest tests/test_subscription_parser.py tests/test_subscription_policy.py tests/test_subscription_host_normalization.py tests/test_subscription_concurrency.py tests/test_uri_list_resilience.py tests/test_uri_protocol_fidelity.py tests/test_uri_golden_contract.py tests/test_vmess_resilience.py -m "not integration"` | 0 | 142 passed |

各命令的完整 stdout / 退出码保存在证据目录对应文件（`ruff-check.txt`、`ruff-format.txt`、`typecheck-windows.txt`、`typecheck-linux.txt`、`pytest-full.txt`、`risk-coverage.txt`、`audit.txt`、`doctor.txt`、`grpc-integration.txt`、`targeted.txt`、`t2-directed.txt`、`t3-directed.txt`）。

### 环境备注（可复核）

- 本沙箱经 `PYTHONPATH` 注入删除守卫；带 `--cov` 运行时 coverage 合并并行数据文件的删除会触发 bulk-delete 守卫，导致 `INTERNALERROR` 与次生失败。以 `env -u PYTHONPATH` 运行得到上表干净结果；不带 `--cov` 的对照运行同样为 1453 passed。
- 未设置 `--basetemp` 时 Windows 临时目录出现 WinError 5；本窗口统一把 pytest 临时目录指向 `.work/acceptance-*/`，属运行环境处置，非源码行为。

### 未执行 / 边界

- GitHub 工作流：新增 `windows-platform` job、validated 聚合与 caller 权限仅经本地 YAML 契约测试验证，未触发远程运行 → NOT_EXERCISED。
- 真实 Cloudflare dry-run、发布、回滚、retention、生产写入 → 未执行。
- Python 3.11 / 3.12 运行、wheel 构建与源码树外 smoke、双次 fixture 字节比较、stable-mihomo 完整矩阵 → 沿用 CI，本地未执行。
- 真实 gRPC 证据为 Windows v1.19.30 单内核；未覆盖 Linux / arm64。
- 上表「通过」仅指本轮新运行，不补造旧日志，也不代表生产发布就绪。

### 下一动作

- 在精确 SHA 上触发 GitHub CI（含新 `windows-platform` job 与 validated 聚合）与 stable-mihomo 矩阵。
- 如需关闭完整协议验收门，补 Linux 内核的 gRPC 实际传输证据。

## 2026-10-14：推送 main、远程 CI 验证与 Windows 门修复（外部验证窗口）

范围：在精确 SHA 上推送 `main` 并触发 GitHub CI，按实际结果修复新增 Windows 门暴露的两处真实缺陷，并取得 Linux 内核 gRPC 证据。push 事件下 `deploy` 被 `github.event_name != 'push'` 排除，未触发部署；未访问生产 KV，未执行发布 / 回滚 / retention。

### 输入与环境

- 起点 HEAD `69427dd`，`origin/main` 原为 `95cad44`。
- 证据目录 `.work/acceptance-20261014-192602/`（推送前基线 `input-manifest.txt`、`env.txt`）。
- 环境：Windows（MINGW64_NT-10.0-26100）、Python 3.13.15、pytest 9.0.2、Ruff 0.14.10、mypy 1.18.2；`gh` 以 `hzoonp` 认证（`repo` + `workflow` 作用域）。本机无 WSL / Docker，无法本地取得 Linux 内核证据。

### 推送前本地门（在 `69427dd` 上重跑）

| 验证 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- |
| Lint | `python -m ruff check .` | 0 | All checks passed |
| 格式 | `python -m ruff format --check .` | 0 | 353 files already formatted |
| 类型（Windows） | `python scripts/run_project_checks.py typecheck` | 0 | Success: no issues found in 49 source files |
| 单测 + 覆盖率 | `env -u PYTHONPATH python -m pytest -m "not integration" --cov=clash_relay --cov-fail-under=75 …` | 0 | 1453 passed、50 deselected；84.09%（146.91s） |
| 高风险覆盖率 | `python scripts/audit_risk_coverage.py <coverage.xml>` | 0 | risk-based coverage ratchet: passed |
| 审计 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | 0 | 子门全部通过；ACL4SSR `matched_with_declared_deviations`，`upstream_reference_verified=false` |
| 公共配置 | `python -m clash_relay.cli doctor --public-only` | 0 | status=passed |

### 第一次推送与 CI 失败（R06 新门暴露真实缺陷）

- 推送 `95cad44..69427dd`，CI run `38558938927`（event=push）。
- 结果 **failure**：Python 3.11 / 3.12 / 3.13、deterministic-generation、routing-required、Mihomo v1.19.29、Mihomo v1.19.30 均 success；**Windows platform gate failure**；`validated` 与 `deploy` 因此 skipped。
- 根因（`gh-windows-failed.txt`）：Windows job 复用 `requirements-dev.lock`，而锁头声明为 Linux x64 runners。pip 在 windows-latest 解析到 `pyyaml-6.0.3-cp312-cp312-win_amd64.whl`，哈希不在锁内 → `THESE PACKAGES DO NOT MATCH THE HASHES`；连锁使 `hatchling` 未安装，`pip install --editable .` 报 `BackendUnavailable: Cannot import 'hatchling.build'`。类型检查与进程测试从未执行。
- 连带影响：定时 run `38559843548`（event=schedule，SHA `69427dd`）在 `trusted-main` 阶段失败（该 SHA 无成功完整验证），其余 job 与 `deploy` 全部 skipped，**未发生生产写入**。

### 修复一：锁文件覆盖 Windows（`10aecc1`）

- 补 Windows x64（CPython 3.12）wheel 哈希。仅 5 个平台相关包需要补：`PyYAML`、`rpds-py`（requirements.lock）、`coverage`、`mypy`、`ruff`（requirements-dev.lock）；其余 17 个为纯 Python wheel，跨平台哈希相同，未改动。锁头改为声明 Linux x64 与 Windows x64 两个支持平台，保持单一锁来源、不新增锁文件。
- Windows job 的安装拆成两个单命令步骤：默认 PowerShell 在多行步骤中不会因原生命令非零退出码中止，拆分后安装失败会直接失败该门，而不是以「缺少构建后端」的形式出现或静默用残缺依赖继续。
- 本地验证：`audit_supply_chain.py` passed；`pip download --platform win_amd64 --python-version 3.12 --abi cp312 -r requirements-dev.lock` 与 `--platform manylinux2014_x86_64` 均成功（哈希校验通过）。
- CI run `38560429609`：安装与类型门通过（`Successfully installed clash-relay-2.3.0`、`Success: no issues found in 49 source files`），pytest 步骤仍失败。

### 修复二：Windows 步骤改用 PowerShell 语法（`17ece93`）

- 根因（`gh-windows-failed-2.txt`）：pytest 步骤使用了 bash 的 `\` 续行，PowerShell 把反斜杠当作普通字符 → `ParserError: Missing expression after unary operator '--'`，pytest 未执行。
- 改为单行命令，并使用 `python -m pytest`，不依赖入口脚本在 PATH 上。
- 本地验证：在 PowerShell 中执行同一条命令 → `9 passed`（`unit-win-sim2.xml`：tests=9、errors=0、failures=0）。不带 `--basetemp` 的本地首跑出现 `WinError 5`，属本机沙箱临时目录限制，非 CI 行为。

### 最终 CI 结果（run `38561302259`，SHA `17ece93`）

| Job | 结论 | 关键证据 |
| --- | --- | --- |
| Python 3.12 quality | success | 覆盖率 84.08%（floor 75%）；ruff / 格式 / 类型 0 问题；risk ratchet passed；8 项审计 passed；wheel 构建与源码树外 smoke 通过 |
| Python 3.11 compatibility | success | 1453 passed、50 deselected |
| Python 3.13 compatibility | success | 1453 passed、50 deselected |
| Windows platform gate | success | `Successfully installed clash-relay-2.3.0`；`Success: no issues found in 49 source files`；`9 passed` |
| Deterministic fictional generation | success | 双次生成字节比较、ACL4SSR 保真、pinned stable 矩阵导出 |
| Verify finalized Routing V2 graph | success | 依赖 quality 的 drift 门 |
| Mihomo v1.19.29 | success | integration `47 passed, 6 deselected`，含 `tests/integration/test_vmess_grpc_transport_mihomo.py ..`；ACL4SSR upstream `3 passed` |
| Mihomo v1.19.30 | success | 同上 |
| Validate exact commit / Validated SHA | success | 「Bind validation result to exact commit SHA」通过 → validated SHA = `17ece93` |
| Run canonical production lifecycle | skipped | push 事件下按设计不部署 |

**R04 的 Linux 内核证据已取得**：`test_vmess_grpc_transport_mihomo.py` 在 ubuntu-latest（linux-amd64）上随 v1.19.29 与 v1.19.30 各执行 2 项并通过。仍缺 arm64 与其他架构。

### 本轮关闭与剩余边界

- 关闭：GitHub CI 未执行 → 已在 `17ece93` 上全绿；Windows 平台门真实生效；`validated` 聚合真实生效（并已实证失败门会阻止 `validated` 与 `deploy`）。
- 关闭：R04 的 Linux（amd64）内核 gRPC 证据。
- 仍未执行：真实 Cloudflare dry-run、发布、回滚、retention、任何生产写入；arm64 内核 gRPC；真实设备客户端验证。
- 本地通过、远程通过、生产状态仍分别判断；本轮未改变生产。

### 下一动作

- 观察下一个定时发布窗口能否在 `validated` 恢复后正常取用验证结果；该路径仍属外部验证，需单独记录。
- 如需 arm64 协议证据，扩展 stable-mihomo 矩阵。

## 2026-10-14：O3 第一批——把 URI / 订阅解析边界纳入类型门

范围：按 PLAN §8 的 O3 要求，先评估 URI / 订阅边界的未覆盖模块及导入，再以零抑制方式完成第一批扩展。未改动业务行为，未触发外部动作。

### 先决：文档窗口的 CI 结果

- run `38562845128`（SHA `a067828`，纯文档提交）→ success；9 个 job 全通过，`validated` SHA = `a067828`。文档提交不改变任何门。

### 评估（mypy 1.18.2，选项与 canonical 门一致）

覆盖率取自本轮 `.work/acceptance-20261014-192602/coverage.xml`；报错数为该模块单独加入类型门时的实际输出。

| 模块 | 覆盖率 | 报错数 |
| --- | --- | --- |
| `errors.py` | 100% | 0 |
| `status.py` | 100% | 0 |
| `schema.py` | 88.57% | 0 |
| `subscription_parser.py` | 88.03% | 0 |
| `uri_parser.py` | 78.92% | 0 |
| `dns_leak_audit.py` | 74.04% | 0 |
| `validator.py` | 73.63% | 0 |
| `util.py` | 88.70% | 2（`yaml` / `yaml.tokens` 缺 stub） |
| `dns_runtime_audit.py` | — | 1（`tuple[str, str, int, str]` 赋给 `tuple[str, ...]`） |

`util.py` 是源码中唯一导入 `yaml` 的模块，正因它此前不在类型目标内，缺 stub 的问题一直未被暴露。

### 第一批实施

- `scripts/run_project_checks.py` 的 `MYPY_TARGETS` 由 49 增至 **56**，新增 7 个零报错模块：`dns_leak_audit`、`errors`、`schema`、`status`、`subscription_parser`、`uri_parser`、`validator`。
- 未新增 `# type: ignore`、未引入 `Any`、未放宽错误码，也未打开全仓 strict。
- 验证（本机新运行）：`python scripts/run_project_checks.py typecheck` → exit 0，`Success: no issues found in 56 source files`；同 56 目标加 `--platform linux` 亦为 0 错误；`ruff check` exit 0；非集成测试 1453 passed。

### 第二批（未实施，已定位）

- `util.py`：需补 `types-PyYAML` stub（连带更新 `requirements-dev.lock` 的 Linux 与 Windows 哈希），并修 `util.py:80` 的联合类型赋值。
- `dns_runtime_audit.py:70`：修 tuple 赋值不兼容。
- 两项都涉及源码或依赖变更，按 O3「分批」原则留作下一批，本轮不做。

### 边界

- 覆盖率数字来自本轮本地 coverage 运行；CI 上为 84.08%（Python 3.12），两者不互相替代。
- 类型门扩展只说明这些模块在现有选项下无静态错误，不代表其运行期行为已被完整验证。

### 本窗口的 CI 结果

- run `38562845128`（SHA `a067828`，纯文档提交）→ success；9 个 job 全通过，`validated` SHA = `a067828`。
- run `38565198668`（SHA `99e7afb`，O3 第一批）→ success；Linux（Python 3.12 quality）与 Windows platform gate 均输出 `Success: no issues found in 56 source files`，`validated` SHA = `99e7afb`。
- 本文档本身随代码提交，承载文档更新的提交由同一条 `publish.yml` 验证链覆盖；其 run 不再逐个回填，以免文档与提交互相追赶。当前 `main` 的精确 SHA 与验证结论以 `gh run list` / GitHub Actions 为准。

## 2026-10-14：O3 第二批——YAML 共享边界与 DNS 运行时审计

范围：完成第一批评估中已定位的第二批——修掉两个真实类型错误、补齐 `types-PyYAML` stub、把两个模块纳入类型门，并为被改动的失败路径补一条精确类别的回归。改动含运行期源码，逐项做了行为等价验证。

### 两处类型错误（零行为变化）

| 位置 | 原报错 | 修法 |
| --- | --- | --- |
| `util.py:80` | `error` 先被推断为 `type[UnsafeSubscriptionError] \| type[ConfigurationError]`，再赋 `SubscriptionError \| ConfigurationError` 不兼容 | 两类失败绑到不同名字：`unsafe_error`（NUL / 锚点别名）与 `invalid_error`（解析失败）；`unsafe_error` 提到循环外 |
| `dns_runtime_audit.py:70` | `authority: tuple[str, ...]` 无法容纳含 port 的 4 元组 | 新增模块级别名 `_ResolverAuthority = tuple[str, ...] \| tuple[str, str, int, str]`，set 与变量共用；两种形状仍不可能相等 |

### 行为等价验证（本窗口新运行）

- `util.yaml_load_no_aliases`：6 条失败路径异常类型逐一不变（NUL / 锚点别名 × trusted / untrusted；畸形 YAML × 两者），2 条成功路径正常加载。
- `dns_runtime_audit._require_distinct_resolver_authorities`：6 条判重用例不变（重复 `system`、重复 URL 报错；不同 URL、同主机不同端口、`system` + URL 不报错）。

### 依赖变更：types-PyYAML

- `requirements-dev.lock` 新增 `types-PyYAML==6.0.12.20260906`（`py3-none-any`，纯 stub，一条哈希覆盖所有平台与 Python 版本）；哈希取自 PyPI 并下载复核为 `bca893ff…5137b`。
- 锁文件约定据实核对：平台相关包按「CI 运行器 = Python 版本 × 系统」各列一条哈希（`PyYAML` 现为 cp311 / cp312 / cp313 的 manylinux_x86_64 加 cp312-win_amd64）。
- 校验：`pip download`（锁内 `--hash` 隐式开启哈希校验）在 `win_amd64` 与 `manylinux2014_x86_64`（cp312）下均成功，并都拉取到该 stub。

### 新增回归（判别力已实证）

- `tests/test_subscription_parser.py` 新增 3 项：untrusted 的 NUL / 锚点别名必须抛 `UnsafeSubscriptionError`（而不是其父类 `SubscriptionError`），untrusted 的畸形 YAML 必须保持可重试的 `SubscriptionError`。
- 该不变量此前无覆盖：既有 `test_yaml_aliases_rejected` 断言的是父类，`UnsafeSubscriptionError` 的子类关系使降级不会被发现；而 `test_production_diagnostics.py` 依赖该类映射到 `subscription_admission`（不可重试）。
- 判别力验证：把 `util.UnsafeSubscriptionError` 临时替换为 `SubscriptionError` 后，同一路径返回 `SubscriptionError` → 新断言会失败；还原后恢复 `UnsafeSubscriptionError`。

### 类型门扩展

- `MYPY_TARGETS` 由 56 增至 **58**，新增 `dns_runtime_audit` 与 `util`。仍无 `# type: ignore`、`Any`、放宽错误码或全仓 strict；`util.py` 的 stub 报错通过补依赖解决，未使用 `disable_error_code`。

### 本地门（本窗口新运行）

| 验证 | 结果 |
| --- | --- |
| `python scripts/run_project_checks.py typecheck` | exit 0，`Success: no issues found in 58 source files` |
| 同 58 目标 + `--platform linux` | exit 0，`Success: no issues found in 58 source files` |
| `ruff check` / `ruff format --check` | All checks passed / 353 files already formatted |
| `pytest -m "not integration"` | 1456 passed、50 deselected |
| `pytest -m "not integration" --cov=clash_relay --cov-fail-under=75` | 1456 passed；总覆盖率 84.09%（floor 75%） |
| `python scripts/audit_risk_coverage.py <coverage.xml>` | risk-based coverage ratchet: passed |
| `audit --offline-acl4ssr` | 8 项子门全部 passed |

### 边界

- 为在本机验证 `util.py`，`types-PyYAML` 装进了系统 Python 3.13 的工具链环境；CI 由锁文件安装，版本一致。
- 类型门扩展只说明静态无错，不代表运行期行为已完整验证；`util.py` 与 `dns_runtime_audit.py` 的行为等价性由上面的显式用例覆盖。

## 2026-10-16：当前项目复查与计划更新

### 输入与工作范围

- HEAD：`cd9c1788db110d1644d47f9c22f22b546f6c2524`（`chore: type-check the YAML boundary and the DNS runtime audit`）；开始时 `git status --short` 为空。
- 环境：Windows PowerShell、Python 3.13.15、pytest 9.0.2、Ruff 0.14.10。未安装或升级依赖。
- 只读检查关键源码、配置、工作流及历史验收记录；最终仅更新本目录三份文档。独立检查分别覆盖发布/网络/脱敏边界和 CI/依赖/维护性，由主检查整合并复现。
- 未读取生产订阅、调用真实 Cloudflare、派发远程工作流或实施业务修复。历史 R01–R06 / O1–O3 保持关闭。

### 实际运行结果

以下均来自本轮工具输出；基线检查的完整 stdout 未另存原始日志，不将本表描述为原始日志归档。合成复现脚本保存在忽略目录 `.work/acceptance-20261016-review/reproduce.py`，便于当前工作区重放；关键输入和结论同时记录在计划中，不依赖本地目录作为唯一证据。

| 命令 | 实际结果 |
| --- | --- |
| `python -m pytest -m "not integration" --durations=12 -q` | exit 0；1456 passed、50 deselected；100.41 秒 |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 353 files already formatted；与前项同一调用，两项均输出成功，整体 exit 0 |
| `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；8 个审计子门通过，仓库安全检查 469 文件；ACL4SSR matched_with_declared_deviations，upstream_reference_verified=false |
| `python -m clash_relay.cli doctor --public-only` | exit 0；status=passed；订阅、Cloudflare、release state 检查 skipped |
| `python .work/acceptance-20261016-review/reproduce.py` | exit 0；下表 R07–R11 及 V01 的旧视图分类复现成功；只使用本地临时目录与合成数据 |

没有代码修改，因此不为计划编制重复整套测试。本轮未采集新 coverage；84.09% 属于 2026-10-14 历史数据。未执行的 50 项 integration 不记为失败，也不记为本轮通过。

### 新发现与证据

| 编号 | 实际观察 | 结论边界 |
| --- | --- | --- |
| R07 | 非数字端口 `bad`、越界端口 `70000` 的合成 URL 经 redact_text 均抛 ValueError | 确认脱敏异常；未宣称发生泄漏 |
| R08 | Schema 接受 427/428/512 字节 ASCII 基键；manifest 分别长 512/513/597 字节，后两者 publisher 校验失败 | 确认校验层不一致；先 config 后 manifest 的部分 staging 风险来自代码顺序，未远程写入 |
| R09 | 命名 step 后的独立 `uses: actions/checkout@main` 通过 `_audit_workflow_actions()` | 确认审计漏检；仓库对应格式的现有 action 固定 SHA |
| R10 | `enabled: false` 后重复 `enabled: true` 被 loader 解析成 `{'enabled': True}` | 确认重复键静默覆盖；未发现当前配置含重复键 |
| R11 | 24 个风险模块的 XML line-rate 全部为 NaN，audit 返回 0 个 failures | 确认异常报告校验缺口；正常 pytest-cov 未见这种产物 |
| V01 | 旧 production / 旧 current / 无可见 transaction 的 fake 读视图返回 not_committed、production_changed=false、requires_manual_action=false | 仅证明该观察会被如此分类；真实服务可达性、事务顺序约束和完整生命周期故障注入待验证 |

辅助检查发现 `CandidateArtifact.document` 的嵌套映射仍可被调用者修改，但当前生产用法只读取其 fingerprint，未找到实际阶段污染路径，因此未列为本轮必修问题。

### 性能与维护性观察

- 4 项发布失败测试各约 7.50 秒，另 6 项各约 3.75 秒，最慢 10 项共约 52.5 秒；发布 read-back 退避序列为 `(0, 0.25, 0.5, 1, 2)`。O4 先针对纯内存测试消除真实等待，并保留重试策略断言；此处未给出尚未测量的修复后速度。
- CI 已有多 Python、Windows、wheel 独立安装与真实 Mihomo 检查；不重复规划这些能力。O5 聚焦依赖元数据/锁/安装一致性，O6 聚焦现有测试报告保存。
- V01 暂列 P1 调查项；R07–R10 为 P2 确认问题，R11 为 P3 加固。实现步骤、依赖与验收条件见 PLAN 第 0 节。

### 交付状态

本轮交付项目检查结果和可执行计划，不把问题登记写成问题已修复。当前待办由 README 页首汇总；真实 CI/生产证据继续与本地证据分开。

文档写入后，`python scripts/audit_documentation_contract.py` 输出 passed，`git diff --check` 无诊断；最终跟踪差异仅涉及本目录 README、PLAN、EXECUTION 三份文档。

## 2026-10-16：R07–R11 / O4–O6 实施与 V01 状态机验证窗口

### 输入与工作范围

- HEAD：`cd9c1788db110d1644d47f9c22f22b546f6c2524`；本窗口全部改动留在工作区，未提交、未推送。
- 环境：Windows、Python 3.13.15（开发环境）、pytest 9.0.2、Ruff 0.14.10、mypy 1.18.2；另用 uv 提供的独立 CPython 3.12.15 解释器验证 Windows 锁安装。
- 实施范围：PLAN §0.2–§0.8 的 R07–R11、O4–O6 与 §0.9 门 2 的 V01 状态机验证。
- 未执行：生产写入、真实 Cloudflare / KV 访问、远程工作流派发、发布 / 回滚 / 清理 / retention 动作。
- 证据目录（git 忽略）：`.work/acceptance-20261016-fix/`。

### 各项：反例 → 改动 → 定向回归

| 项 | 修复前反例（实测） | 实现改动 | 修复后定向回归 |
| --- | --- | --- | --- |
| R07 脱敏异常端口 | 非数字端口 `bad`、越界端口 `70000` 的合成 URL 经 `redact_text` 抛 `ValueError`（复查窗口 `reproduce.py` 复现） | `src/clash_relay/redact.py` 把 hostname/port 提取纳入畸形输入守卫；解析失败返回固定占位符，保留正常 IPv4/IPv6 origin 诊断 | `pytest tests/test_status_and_security.py tests/test_mihomo_unit.py -q` → 49 passed；模拟 Mihomo 失败输出仍抛本项目错误，且不含合成凭据 / 路径 / 查询 |
| R08 派生 KV 键长度 | Schema 接受 427/428/512 字节基键，manifest 长至 513/597 字节后在 publisher 处失败；`release_bundle` 可能已 stage config 对象 | 新增 `src/clash_relay/kv_keys.py`：所有派生名唯一声明 + `oversized_derived_kv_keys()` 字节长度预检，由 config 语义检查、doctor 与发布入口共用；底层 publisher 保留自身 512 字节自检 | `pytest tests/test_kv_key_capacity.py -q` → 19 passed：427/428 字节与多字节中文基键、全后缀组合、非法输入在 fake publisher 首次读 / 写之前失败 |
| R09 供应链审计漏检 | 命名 step 之后的独立 `uses: actions/checkout@main` 通过 HEAD 的 `_audit_workflow_actions()` | `scripts/audit_supply_chain.py` 改为结构化解析每个 job 的 `uses` 与 `steps[*].uses`（`.yml` 与 `.yaml` 都扫描）；区分本地 `./` 与外部引用，job 级外部引用要求不可变 SHA，解析失败即审计失败，`docker://` 只接受 digest | `pytest tests/test_supply_chain_audit.py -q` → 33 passed；把脚本回退到 HEAD 后同样用例 28 failed、5 passed（`r09-evidence.txt`） |
| R10 YAML 重复键 | `enabled: false` 后重复 `enabled: true` 被 PyYAML 解析成 `{'enabled': True}`，冲突声明在任何 schema 检查之前消失 | `src/clash_relay/util.py` 用 `_StrictMappingLoader` 在映射构造处拒绝重复键，`yaml_load_no_aliases` 接入并只报告行列、不回显键值；config 与订阅文档共用同一入口 | `pytest tests/test_config_loader.py tests/test_subscription_parser.py -q` → 97 passed；重复键在 config 与订阅两侧都归入既有 unsafe 异常族 |
| R11 覆盖率比例校验 | 24 个风险模块的 XML `line-rate` 全部为 NaN 时 audit 仍返回 0 个 failures | `scripts/audit_risk_coverage.py` 要求属性存在、可解析为有限值且落在 `[0, 1]`，缺失 / NaN / ±Inf / 越界一律失败 | `pytest tests/test_risk_coverage_audit.py -q` → 23 passed；真实 pytest-cov 报告（119 个 `line-rate` 属性）仍通过 |
| O4 测试真实等待 | 4 项发布失败测试各约 7.50 秒、6 项各约 3.75 秒（最慢 10 项 ≈52.5 秒）来自真实退避；整轮非集成 `100.41s` | 纯状态机测试注入 read-back 等待（`release_read_back_waits` fixture），**保留**重试次数 / 退避序列 / 停止条件断言，并新增 `test_production_wait_seam_actually_sleeps` 证明生产装配仍是真睡眠 | `pytest tests/test_release_transaction_recovery.py tests/test_release_bundle.py tests/test_release_bundle_failure_boundaries.py tests/test_ambiguous_release_recovery.py -q` → 61 passed in 0.23s；整轮非集成 `27.69s`，最慢用例 0.76s |
| O5 依赖 / 锁 / 安装一致性 | 预检门在三种漂移下均 exit 0：元数据只漂移一侧、锁只漂移一侧、dev 缓存键漏 `requirements-dev.lock`（`o5-evidence.txt` A/B）；真实环境另发现 Windows 开发环境 `pip check` 失败（`pytest` 需要 `colorama`），干净 CPython 3.12 venv 无法用 `--require-hashes` 安装 `requirements-dev.lock` | `scripts/audit_supply_chain.py` 新增：依赖元数据 ↔ 双锁版本比对（PEP 503 归一化）、ci.yml 开发与 standalone wheel 环境必须 `pip check`、每个 job 的 pip 缓存键必须覆盖该 job 安装的全部锁；`requirements-dev.lock` 补 `colorama==0.4.6 ; sys_platform == "win32"`（含 SHA-256）；ci.yml 4 个开发 job 缓存键同时覆盖两份锁并各自 `pip check` | 修复后 A–F 六组证据全部按预期拒绝漂移（`o5-evidence.txt`）；干净 venv 安装 + 开发 / wheel 两个环境 `pip check` 均 exit 0（`o5-runtime-evidence.txt`）；`pytest tests/test_supply_chain_audit.py -q` → 33 passed |
| O6 CI 报告留存 | 成功与失败路径都没有报告归档步骤，ACL4SSR 重试三 attempt 共用同一 JUnit 路径；把 ci.yml 回退到 HEAD 后 10 项契约中的 8 项失败 | ci.yml 增加 8 个 `actions/upload-artifact`（成功与 `if: failure()` 同名成对、`retention-days: 7`、白名单路径），ACL4SSR 重试按 attempt 写不同文件；归档范围排除生成配置、订阅源、smoke 工程、fixture 密钥文件与 `.work` 其余内容 | `pytest tests/test_ci_report_retention.py -q` → 10 passed；未引入 `always()` 或 `continue-on-error`，维持仓库既有机械不变量 |

R08 按 PLAN 明确要求**没有**只把 JSON Schema 字符上限改成 427：长度门按 UTF-8 字节判定派生名集合，多字节基键同样被拦。O4 未以删除重试语义换速度：退避序列 `(0, 0.25, 0.5, 1, 2)`、最大次数与停止条件断言全部保留，另有一项测试守住生产装配的真睡眠。

### 反例的批量负向对照

把 `src/` 与 `scripts/` 整体回退到 HEAD（`kv_keys.py` 一并移出）后运行本窗口新增 / 扩展的用例：

| 用例文件 | 回退源码上的结果 |
| --- | --- |
| `tests/test_kv_key_capacity.py` | 1 error（`clash_relay.kv_keys` 不存在） |
| `tests/test_config_loader.py` / `tests/test_subscription_parser.py` | 2 failed、26 passed / 1 failed、68 passed |
| `tests/test_status_and_security.py` / `tests/test_mihomo_unit.py` | 6 failed、34 passed / 1 failed、8 passed |
| `tests/test_risk_coverage_audit.py` | 15 failed、8 passed |
| `tests/test_supply_chain_audit.py` | 28 failed、5 passed |
| `tests/test_stale_read_reconciliation.py` | 8 errors（新 API 缺失） |
| `tests/test_release_bundle.py` 等 3 个发布用例文件 | 1 failed、8 passed、9 errors 等 |

汇总 54 failed、193 passed、22 errors（`batch-negative-control.txt`）；恢复实现后上述定向集合全绿。两侧文件的部分用例在回退后仍通过属于预期：它们断言的是修复前就已成立的行为（例如既有错误族、既有发布契约）。

### V01：状态机可达性与生命周期证据

状态模型（`tests/test_stale_read_reconciliation.py`，新增）把“远端实际持有的内容”与“某条观察路径返回的视图”分离：`LaggingKV.storage` 是存储真值，`LaggingKV.view` 是本次 reconciliation 看到的快照，`stale` 集合让已接受的写保持不可见，`converge()` / `use_view()` 分别模拟传播完成与换用一条更旧的路径。

- **可达性结论**：对“候选仍存活”的第二次发布，`not_committed`（`pre_attempt_production_still_live`、`production_changed=false`、`requires_manual_action=false`）只在观察路径**同时**看不到 pending transaction、已接受的 activation 写和 staged pointer 时产生——即可见性相对尝试时发生回退。依据：`test_observation_path_that_never_saw_the_attempt_reports_not_committed` 断言该视图下返回 `not_committed`，同时断言 `storage[production-config] == CANDIDATE` 且 pending transaction 的 `to_release_id == sha256(CANDIDATE)`。反例边界：同一路径上 pending 可见时返回 `unknown`（`transaction_incomplete_or_not_yet_visible`），首次发布在滞后激活下**不会**被判 `not_committed`，确凿拒绝仍应报 `not_committed`（`reject_once` 场景，存储仍为旧值），因此该状态并未被笼统禁止。
- **生命周期证据**：把真实 reconciliation 与 `ProductionPipeline._preserve_ambiguous_release` 串起来跑（`_lifecycle_evidence`），三条轨迹分别得到 `preserved`（保留 `candidate.yaml` / `previous.yaml` / `metadata.json`，`reconciliation_status == "unknown"`）、`committed`（收敛后尝试完成，不保留副本）与 `not_committed`（**不保留任何副本**，而 `storage` 仍为候选）。第三条即 V01 的后果面：一旦陈旧视图把存活候选判成未提交，唯一的私有副本会被丢弃，远端却已是候选。
- **结论与状态**：本地状态机证据支持“陈旧视图可导致 `not_committed` 且丢弃私有恢复副本”这一可达性判断，并给出现成的回归护栏；但**没有**任何真实 Cloudflare 读一致性、传播延迟或事务顺序证据，也未做生产故障注入。因此 V01 维持**独立、待验证**状态，不写为已修复、不并入本批已关闭项（PLAN §0.9 门 2 原样保留）。

### §0.9 实施完成门：实际命令与退出码

全部在工作区内容上运行，完整转录 `gate-run.txt`（含 stdout）：

| # | 命令 | 退出码 | 范围与结果 |
| --- | --- | --- | --- |
| 1 | `python -m ruff check .` | 0 | All checks passed! |
| 2 | `python -m ruff format --check .` | 0 | 359 files already formatted |
| 3 | `python scripts/run_project_checks.py typecheck` | 0 | Success: no issues found in 58 source files |
| 4 | `python -m pytest -m not integration -p no:cacheprovider --basetemp=.work/acceptance-20261016-fix/basetemp-gate --cov=clash_relay --cov-fail-under=75 --cov-report=term-missing --cov-report=xml:.work/acceptance-20261016-fix/coverage-3.13-local.xml --junitxml=.work/acceptance-20261016-fix/unit-3.13-local.xml` | 0 | 1567 passed、50 deselected、121.47 秒；TOTAL 84%（要求 75%，实际 84.21%）；XML 写入证据目录 |
| 5 | `python scripts/audit_risk_coverage.py .work/acceptance-20261016-fix/coverage-3.13-local.xml` | 0 | 24 个高风险模块全部达标（最低 `production_lifecycle.py` 76.19% / 门 70%），risk-based coverage ratchet: passed |
| 6 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | 0 | 8 个审计子门通过；仓库安全检查 475 文件；ACL4SSR matched_with_declared_deviations、upstream_reference_verified=false |
| 7 | `python -m clash_relay.cli doctor --public-only` | 0 | status=passed；subscriptions、cloudflare、release_state 均 skipped |
| 8 | `python scripts/repository_audit.py` | 0 | repository safety audit passed (475 files checked) |
| 9 | `python scripts/audit_supply_chain.py` | 0 | supply-chain audit: passed |

第 4 项的 121.47 秒包含 coverage 插桩，与复查窗口的 100.41 秒（无 coverage）不是同一口径，因此 O4 的对比统一使用无插桩的 `--durations=12` 运行：基线 100.41 秒 / 最慢 10 项 ≈52.5 秒 → 本窗口 27.69 秒 / 最慢单项 0.76 秒（`o4-durations.txt`），且本轮用例数由 1456 增至 1567。

### 本窗口改动文件

- 实现：`src/clash_relay/{redact,util,config_loader,release_bundle,production_application,production_observability,production_failure_metrics}.py`、新增 `src/clash_relay/kv_keys.py`。
- 审计脚本：`scripts/audit_{risk_coverage,supply_chain,architecture_contract,operational_slo_contract}.py`。
- 工作流与锁：`.github/workflows/ci.yml`、`requirements-dev.lock`。
- 测试：新增 `tests/test_{kv_key_capacity,risk_coverage_audit,supply_chain_audit,ci_report_retention,stale_read_reconciliation}.py`，扩展 `tests/conftest.py` 与 `tests/test_{status_and_security,mihomo_unit,config_loader,subscription_parser,release_bundle,release_bundle_failure_boundaries,release_transaction_recovery,production_metrics}.py`。
- 文档：`docs/publishing.md` 记录 R08 的派生键字节预检与 V01 的 `not_committed` 复核要求；`docs/architecture.md` 与 `docs/security.md` 记录 R10 的重复键拒绝；`docs/security.md` 另记录 O6 的归档白名单与保留期。
- 后两项 `audit_architecture_contract.py` / `audit_operational_slo_contract.py` 的改动只是跟随 R08 的常量归位（`kv_keys.py` 中的规范后缀），不是新增门。

### 定向回归计数（本窗口在修复后重新运行）

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| R07 | `pytest tests/test_status_and_security.py tests/test_mihomo_unit.py -q` | 49 passed in 0.40s |
| R08 | `pytest tests/test_kv_key_capacity.py -q` | 19 passed in 0.11s |
| R09 + O5 | `pytest tests/test_supply_chain_audit.py -q` | 33 passed in 0.18s |
| R10 | `pytest tests/test_config_loader.py tests/test_subscription_parser.py -q` | 97 passed in 1.19s |
| R11 | `pytest tests/test_risk_coverage_audit.py -q` | 23 passed in 0.08s |
| O4 | `pytest tests/test_release_transaction_recovery.py tests/test_release_bundle.py tests/test_release_bundle_failure_boundaries.py tests/test_ambiguous_release_recovery.py -q` | 61 passed in 0.23s |
| O6 | `pytest tests/test_ci_report_retention.py -q` | 10 passed in 0.15s |
| V01 | `pytest tests/test_stale_read_reconciliation.py -q` | 8 passed in 0.08s |

### 边界与未执行项

- 50 项 `integration` 用例本窗口未运行（`-m "not integration"` 之外），既不记为通过也不记为失败；R04 遗留的 arm64 与真实设备验证仍未覆盖。
- 改动未提交、未推送：远程 CI 的最新结果（`38565198668`）对应 HEAD `cd9c178`，**不**代表本工作区内容；本批 R07–R11 / O4–O6 的远程 CI 结论待推送后单独记录，本地结果与远程结果分开陈述。
- 未访问真实 Cloudflare / KV，未执行发布、回滚、清理或 retention；O6 只让 CI 归档测试报告，尚未在真实 CI 上产生一次“失败仍保留”的实例。
- 未扩大依赖升级范围：除 Windows-only 传递依赖 `colorama` 的补齐外没有改动任何依赖版本；`pip check` 与缓存键规则只强制 ci.yml 的开发与 standalone wheel 安装环境。
- 全树 `uses` 引用审计通过，但本窗口没有把 `pip check` 要求推广到 `publish.yml` / `rollback.yml` / `retention.yml` / `release-inventory-audit.yml`（这四份工作流安装运行时锁但没有 `pip check`）。这是观察到的加固余量，未列为本轮修复项，供下一窗口决定。
- 真实服务侧结论（生产 KV 行为、传播延迟、发布 / 回滚演练）仍属 NOT_EXERCISED。

## 2026-10-16：实施后独立复审

输入为 HEAD `cd9c1788db110d1644d47f9c22f22b546f6c2524` 加已有未提交实现/测试/文档。读取 status、diff 和新增文件后按计划逐项核对，并独立检查 CI 与发布恢复边界。保护全部已有修改；本轮只更新 README、PLAN、EXECUTION，不修代码、不提交、不推送，也不执行生产动作。

### 本轮真实检查

| 命令 / 范围 | 结果 |
| --- | --- |
| `python -m pytest -m "not integration" --durations=8 -q` | exit 0；1567 passed、50 deselected；48.17 秒；最慢单项 1.35 秒 |
| `python -m ruff check .` / `python -m ruff format --check .` | 分别输出 All checks passed / 359 files already formatted；同一工具调用整体 exit 0 |
| `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；全部子门通过，仓库检查 475 文件；上游 ACL4SSR 在线验证仍为 false |
| 合成审查反例 | 下述 action glob、目录枚举、单 job 检查遗漏、V01 两条旧视图轨迹均已实际运行 |

本轮未重采 coverage、安装依赖矩阵、执行真实 Mihomo integration 或远程 CI。48.17 秒与前一窗口 27.69 秒有环境/负载差异，不将任一单次运行视为固定性能承诺。现有单元与静态门全绿，不等于下面的问题已被覆盖。

### 复审发现与证据校正

1. **O6 / P2：integration 报告实际上被隐藏目录过滤。** `ci.yml:329–343` 两个上传步骤同时声明显式 XML 和 `.work/integration-acl4ssr-…-attempt-*.xml`；没有 `include-hidden-files: true`。实际使用固定 action 所依赖的 `@actions/glob@0.6.1`，在含两个 XML 的临时目录运行相同模式：`excludeHiddenFiles: true` 的搜索根为 `.work`、结果 `[]`；设为 false 返回两个 XML。显式单文件路径对照可匹配，因此本结论限于 stable-mihomo 的两步，不能扩大为全部报告丢失。`if-no-files-found: warn` 使遗漏只产生警告。依据：[固定 upload-artifact 的隐藏文件说明](https://github.com/actions/upload-artifact/blob/cf430e030ddbb5b0abf93d22962f4752f3646cd9/README.md#uploading-hidden-files) 及实际 glob 运行；当前 fnmatch 契约测试未模拟此规则。临时工具证据位于 `C:/Users/Healer/AppData/Local/Temp/clash-ci-glob-review-ec3a435dd2514b04a72b9760c10072fc`，不是长期仓库产物。
2. **R12 / P2：新增恢复测试依赖未规定的目录顺序。** `test_stale_read_reconciliation.py:271` 直接比较 `Path.iterdir()` 的列表顺序。将枚举替换为同一目录内容的反向字母序，再直接运行该测试函数，得到 AssertionError；仅改变合法枚举顺序，文件内容与业务行为相同。Windows 本轮默认枚举通过不证明 Linux 同样通过；尚未在 Linux 实跑失败。应排序或比较集合。
3. **O5 / P3：单个 job 缺少 pip check 仍通过审计。** 在临时目录复制两份锁及真实 ci.yml，只删除 windows-platform 的 `python -m pip check` 步骤；实际 `_audit_install_environment()` 成功返回。原因是 `audit_supply_chain.py:286–296` 聚合整份工作流命令，其他 job 的检查可满足条件。当前工作流本身仍有各项检查，故这是防回归护栏缺口，不是当前依赖冲突。
4. **V01：仍开放，原证据表述过强。** 使用新增 LaggingKV fake、真实发布与 reconciliation，在第二次发布遇到 activation 旧读后，仅将 `view[transaction]` 设为空值，结果已是 `not_committed`，而 `storage[production]` 为候选。current 此时尚未更新，无需假定“已更新 pointer 再回退”。首次发布后切换到 `{}` 旧视图，同样返回 `not_committed` 且远端模型持有候选。两条路径本地运行均 exit 0；真实 REST 是否产生这种视图仍未实证。

V01 的测试 `:252` 只调用 `_preserve_ambiguous_release()`，并未执行 `run()` 的异常捕获和 finally。因此实际动态证据是“不生成 recovery 副本”，不是“已经执行删除唯一私有副本”；`:838` 的删除是静态代码证据，KV 中 immutable 对象也未因此被删除。`:221` 和 `:294–296` 将误判/空副本作为成功期望，属于现状刻画，不是修复验收护栏。此前“首次发布不会误判”应限定为 pending intent 仍可见的那条轨迹。

### 结论

R07–R11 和 O4 未发现新的实现回归。O6 重新打开，R12 新增，O5 增加局部护栏待办，V01 继续独立开放；后续动作与验收写入 PLAN §0.1a。计划尚未完全闭环。所有业务、工作流与测试文件保持复审开始时的用户修改状态。

## 2026-10-16：V01 / O6 / R12 / O5 实施窗口

输入为 HEAD `cd9c1788db110d1644d47f9c22f22b546f6c2524` 加既有未提交实现（复审发现见上一节）。本窗口只处理 PLAN §0.1a 的四项；没有访问真实 Cloudflare / KV，没有派发远程工作流，没有执行发布、回滚、清理或 retention，也没有提交或推送。证据目录 `.work/acceptance-20261016-01a/`。

注意两处与 HEAD 的差异，用于正确理解下面的反例：

- HEAD 的 `ci.yml` **没有**任何 `upload-artifact` 步骤（`git show HEAD:.github/workflows/ci.yml | grep -c upload-artifact` = 0），上传步骤本身是上一窗口未提交的实现；因此 O6 的“修复前”对照取“有上传步骤、无隐藏文件选项”的变体（`ci-without-hidden-optin.yml`），而不是 HEAD。
- HEAD 的 `scripts/audit_supply_chain.py` 没有安装环境审计函数；O5 的“修复前”对照取本窗口编辑前的按全工作流判定的变体（`audit-workflow-wide.py`，由 `build-o5-negative-control.py` 从当前脚本替换单函数生成）。

### 逐项：反例 → 改动 → 定向回归

| 项目 | 反例（对照变体上实际失败） | 改动 | 定向回归（修复后） |
| --- | --- | --- | --- |
| V01 | `lifecycle-pre-v01.py`（`not_committed` 也提前返回）：2 failed / 12 passed，失败项为 `test_lifecycle_keeps_the_only_private_copy_when_the_read_path_lagged`、`test_run_keeps_the_recovery_copy_and_cleans_the_private_directory` | 只有 `committed` 才释放恢复副本；`not_committed` 与 `unknown` 一样保留候选与旧值并写入 `reconciliation_status`；诊断白名单移除 `not_committed` | `pytest tests/test_stale_read_reconciliation.py -q` → 14 passed |
| O6 | 上一窗口的 ci.yml（上传步骤齐备、无 `include-hidden-files`）：4 failed / 30 passed | 8 个上传步骤加 `include-hidden-files: true`；契约测试改为按固定 action 的 glob 规则计算匹配集 | `pytest tests/test_ci_report_retention.py -q` → 34 passed |
| R12 | `stale-read-test-unsorted.py`（回到 `iterdir()` 顺序比较）在反向枚举下：1 failed / 13 passed | 新增 `_copy_names()` 排序比较与 `test_recovery_listing_does_not_depend_on_enumeration_order`；未调整生产文件创建顺序 | 同上 14 passed |
| O5 护栏 | `audit-workflow-wide.py`（全工作流命令集合判定）：6 个“删除单个 job 的 pip check”用例与“其他 job 代偿”用例全部 DID NOT RAISE；7 failed / 1 passed（未改动的工作流仍通过） | `_audit_ci_pip_checks()` 按 job 与解释器判定：谁安装依赖，谁必须在同一 job 运行 `<interpreter> -m pip check` | `pytest tests/test_supply_chain_audit.py -q` → 41 passed |

### V01（P1）：判定不明时不再丢弃恢复副本

- **改动**：`src/clash_relay/production_lifecycle.py` 的 `_preserve_ambiguous_release()` 现在只在 `status == "committed"`（`:624`）时提前返回 `committed`；`not_committed`、`unknown`、`read_failed` 都落到保留路径，写入 `metadata.json` 的 `reconciliation_status`。方法 docstring 记录理由：写结果本已不明，一次只读观测无法给自己的观测路径定日期，仍显示尝试前字节的读可能来自已经滞后于本次尝试已验证写入的路径。`src/clash_relay/production_diagnostics.py:378–384` 的 `recovery_status` 白名单同步去掉 `not_committed`，避免诊断把“已释放副本”写成可能状态。
- **`run()` 全链路证据**：新增 `_drive_run()`，在真实 `ProductionPipeline.run()` 上注入发布阶段失败（异常 → 保留 → `finally`），三条轨迹：
  1. 判定不明（陈旧旧视图使真实 reconciliation 报 `not_committed`）：`recovery_status == "preserved"`、`candidate_release_id` 存在、`paths.private_dir` 已被 `finally` 清理（`production_lifecycle.py:847`）、恢复目录含 `candidate.yaml` / `previous.yaml` / `metadata.json`、`metadata["reconciliation_status"] == "not_committed"`；
  2. 已提交：`recovery_status == "committed"`、不产生恢复目录、private 目录仍被清理；
  3. 确定拒绝（`_release_candidate_stage` 抛 `PublicationError`）：异常原样抛出、无 `recovery_status` 属性、无副本、`store.storage == {}`。
- **刻画与验收分离**：`test_hiding_only_the_pending_intent_is_enough_to_report_not_committed`、`test_an_empty_first_release_view_reports_not_committed_for_a_live_candidate`、`test_observation_path_that_never_saw_the_attempt_reports_not_committed` 保留为“该观察会被如此分类”的现状刻画，不再承担验收；验收由生命周期级与 `run()` 级测试承担。`test_lifecycle_retains_no_evidence_when_the_observation_path_regressed` 已由 `test_lifecycle_keeps_the_only_private_copy_when_the_read_path_lagged` 取代，其期望从“不保留副本”改为“保留副本并记录判定”。
- **仍未验证**：真实 REST 读一致性、传播延迟、事务顺序约束与生产故障注入。本地只证明“状态机在该输入下不再丢弃唯一私有副本”，不写为生产事故已消除。

### O6（P2）：隐藏目录下的归档匹配集

- **固定 action 的规则**：`actions/upload-artifact@cf430e03… # v7.0.2` 的 `src/shared/search.ts` 以 `glob.create(searchPath, {excludeHiddenFiles: !includeHiddenFiles, …})` 建 globber，`include-hidden-files` 缺省为 false。
- **独立复核**（本机 `@actions/glob@0.6.1`、Windows、Node v24.18.0；探针 `glob-probe.mjs`、原始输出 `o6-glob-probe.txt`）：`excludeHiddenFiles=true` 时 ①`.work/integration-*.xml`、`.work/*.xml`、`.work/**/*.xml`、`.w*/unit-3.12.xml`、`.hidden-dir/*.xml` 全部返回 `[]`；②字面路径 `.work/integration-v1.xml`、`.work/unit-3.12.xml`、`.hidden-dir/literal.xml` 仍匹配；③字面路径但自身 basename 隐藏（`reports/.hidden.xml`）返回 `[]`；④可见目录下的通配符只匹配可见文件（`reports/*.xml` → `reports/visible.xml`）；`excludeHiddenFiles=false` 时以上全部匹配。探针用的临时目录位于 `%TEMP%/clash-ci-glob-review-ec3a435dd2514b04a72b9760c10072fc`，不是长期产物；脚本与输出已复制进证据目录。
- **测试**：`tests/test_ci_report_retention.py` 用 `Declaration` / `_glob_matches()` 复刻上述规则，并冻结 18 条语义矩阵（`PINNED_GLOB_SEMANTICS`，即上面 ①②③④ 的可执行形式）。新增合成 `.work` 清单（8 个报告 + 9 个干扰项：fixture 秘密、生成配置、构建 JSON、下载的内核、wheel venv、acceptance 目录、非 XML），断言“所有上传声明的匹配集恰好等于报告白名单”。失败路径孪生改为整份 `with` 比对；三次 attempt 逐个断言“写入的路径被归档”；新增显式规则“`.work/` 下的白名单声明必须开 `include-hidden-files`”，并有一条刻画测试固定“字面路径本身能被匹配、只有显式规则能拦住它”这一不对称。变异用例由 6 个扩到 10 个（新增 `hidden_optin_removed`、`hidden_optin_widened_to_every_file`、`failure_path_without_opt_in`、`narrowed_retry_glob`）。
- **变更面**：`.github/workflows/ci.yml` 的 8 个上传步骤（`include-hidden-files: true` 位于 `:108`、`:119`、`:155`、`:164`、`:212`、`:221`、`:340`、`:351`）；具体 XML 白名单、7 天保留、固定 SHA、成功 / `if: failure()` 成对结构不变；`docs/security.md` 记录为什么必须显式开启隐藏搜索。
- **未执行**：真实 CI artifact 检查（需派发远程工作流）。本地只证明契约与匹配集，不证明真实上传结果。

### R12（P2）：恢复测试不再依赖目录枚举顺序

- `tests/test_stale_read_reconciliation.py` 新增 `_copy_names()`（排序）并在全部副本内容断言上使用；新增 `test_recovery_listing_does_not_depend_on_enumeration_order`：先按默认枚举跑一条轨迹，再把 `Path.iterdir` 打成同一目录的反向字母序重跑，两次的副本名集合必须一致，且都等于 `["candidate.yaml", "metadata.json", "previous.yaml"]`。
- 生产侧未改动任何文件创建顺序。反向枚举对照运行即在 Windows 本机完成；跨平台结论依赖“断言与顺序无关”这一性质，Linux/Windows 门本窗口未派发，不写成已跨平台验证。

### O5 护栏（P3）：pip check 判定按 job 与安装环境

- `scripts/audit_supply_chain.py` 删除全工作流命令集合的 `_workflow_run_commands()`，改为 `_job_pip_usage()` + `_audit_ci_pip_checks()`：逐 job 取 `run` 步骤中以 `-m pip install|check` 开头的行，记录每个解释器是否安装过、是否检查过；凡安装过依赖（带 `--requirement`）却未在同一 job 运行 `<interpreter> -m pip check` 的，审计失败，并用安装的锁给出环境名（`requirements-dev.lock` → development，`wheel-venv` → standalone wheel，其余 → runtime）。判定范围仍限于 `ci.yml`：四份生产工作流各装一份运行时锁、其 `pip check` 加固是单独议题，未被本窗口顺带扩大。
- 反例：`tests/test_supply_chain_audit.py` 用真实 ci.yml 的文本，逐个把 quality（含 standalone wheel）、python-compat、windows-platform、deterministic-generation、stable-mihomo 的检查行替换为 `true`，各自都必须触发审计失败；另有一条“在别的 job 追加等价检查”的用例证明不能代偿；夹具 `_ci_workflow` 的 wheel 步骤补上真实安装行，使规则由安装驱动而非命令出现与否驱动。
- **仍开放**：`publish.yml` / `rollback.yml` / `retention.yml` / `release-inventory-audit.yml` 安装运行时锁但没有 `pip check`（复审窗口记录的加固余量）。本窗口按 §0.1a 只修判定粒度，未扩大范围。

### §0.9 实施完成门：实际命令与退出码

全部在工作区内容上运行，完整转录 `gate-run.txt`（含 stdout）：

| # | 命令 | 退出码 | 范围与结果 |
| --- | --- | --- | --- |
| 1 | `python -m ruff check .` | 0 | All checks passed! |
| 2 | `python -m ruff format --check .` | 0 | 359 files already formatted |
| 3 | `python scripts/run_project_checks.py typecheck` | 0 | Success: no issues found in 58 source files |
| 4 | `python -m pytest -m "not integration" -p no:cacheprovider --basetemp=.work/acceptance-20261016-01a/basetemp-gate --cov=clash_relay --cov-fail-under=75 --cov-report=term-missing --cov-report=xml:.work/acceptance-20261016-01a/coverage-3.13-local.xml --junitxml=.work/acceptance-20261016-01a/unit-3.13-local.xml` | 0 | 1605 passed、50 deselected、102.04 秒；TOTAL 84%（要求 75%，实际 84.32%）；XML 写入证据目录 |
| 5 | `python scripts/audit_risk_coverage.py .work/acceptance-20261016-01a/coverage-3.13-local.xml` | 0 | 24 个高风险模块全部达标（最低比例 `transport_qualification.py` 54.98% / 门 50%；最小余量 `production_diagnostics.py` 95.57% / 门 95%，本轮 V01 修改文件），risk-based coverage ratchet: passed |
| 6 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | 0 | 8 个审计子门通过；仓库安全检查 475 文件；ACL4SSR matched_with_declared_deviations、upstream_reference_verified=false |
| 7 | `python -m clash_relay.cli doctor --public-only` | 0 | status=passed；cloudflare、subscriptions、release_state 均 skipped |
| 8 | `python scripts/repository_audit.py` | 0 | repository safety audit passed (475 files checked) |
| 9 | `python scripts/audit_supply_chain.py` | 0 | supply-chain audit: passed |

第 4 项的 102.04 秒含 coverage 插桩，与复审窗口的 48.17 秒（无插桩）不是同一口径；用例数由复审窗口的 1567 增至 1605（+38 = V01/R12 六项 + O6 二十四项 + O5 八项）。

上表为 run A2。文档更新完成后又在最终树（代码、测试、工作流自 run A2 起未变，仅本目录三份文档有改动）重跑同一脚本，完整转录 `gate-run-b.txt`：9 行仍全部退出码 0，1605 passed、50 deselected、83.11 秒，覆盖率 84.32%，高风险模块 24/24（逐项比例与 run A2 完全一致），文档契约、架构、SLO、客户端 / DNS、服务资格、v2 发布、供应链、仓库安全（475 文件）八项审计与 doctor `passed` 均通过。两次运行的用例数与覆盖率相同，墙钟时间 102.04 秒 / 83.11 秒的差异属环境与负载波动（两次都含插桩），不将任一次视为固定性能承诺。本节的最后一次文字修订完成后，再单独复跑文档契约、仓库安全（475 文件）与供应链三项审计（`gate-run-b.txt` 的 `[C1]`–`[C3]`），退出码均为 0。

### 本窗口改动文件

- 实现：`src/clash_relay/production_lifecycle.py`（V01 保留语义）、`src/clash_relay/production_diagnostics.py`（恢复状态白名单）。
- 工作流：`.github/workflows/ci.yml`（8 个上传步骤加 `include-hidden-files: true`）。
- 审计脚本：`scripts/audit_supply_chain.py`（按 job / 安装环境判定 pip check；删除全工作流命令集合辅助函数）。
- 测试：`tests/test_stale_read_reconciliation.py`（V01 验收 + R12 + `run()` 全链路，8 → 14 项）、`tests/test_ci_report_retention.py`（重写为 glob 语义匹配集契约，10 → 34 项）、`tests/test_supply_chain_audit.py`（逐 job 检查反例，33 → 41 项）。
- 文档：`docs/publishing.md`（歧义提交后只对已证实的 `committed` 释放恢复副本，`not_committed` 视为不可定日期）、`docs/security.md`（隐藏目录搜索必须显式开启）。

### 定向回归计数（本窗口在修复后重新运行）

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| V01 + R12 | `pytest tests/test_stale_read_reconciliation.py -q` | 14 passed in 1.65s |
| V01 相邻 | `pytest tests/test_ambiguous_release_recovery.py tests/test_production_diagnostics.py -q` | 31 passed in 0.11s |
| V01 相邻（生命周期 / 恢复 / 对账） | `pytest tests/test_production_lifecycle_failure_boundaries.py tests/test_production_lifecycle_result.py tests/test_production_lifecycle_unit.py tests/test_production_preflight_lifecycle.py tests/test_recovery_workflow.py tests/test_release_id_reconciliation.py tests/test_release_reconciliation.py tests/test_release_transaction_recovery.py -q` | 88 passed in 0.33s |
| O6 | `pytest tests/test_ci_report_retention.py -q` | 34 passed in 0.25s |
| O6 工作流相邻契约 | `pytest tests/test_repository_safety.py tests/test_workflow_permission_contract.py tests/test_ci_integration_retry_contract.py tests/test_supply_chain_audit.py tests/test_validation_authority.py tests/test_governance_contract.py tests/test_governance_required_checks.py tests/test_v2_fresh_fork.py tests/test_routing_v2_final_contract.py -q` | 64 passed in 2.05s |
| O5 | `pytest tests/test_supply_chain_audit.py -q` | 41 passed in 0.62s |

### 边界与未执行项

- 50 项 `integration` 用例本窗口未运行（`-m "not integration"` 之外），既不记为通过也不记为失败。
- 改动仍未提交、未推送；远程 CI 最新记录（`38565198668`，HEAD `cd9c178`）**不**代表本工作区内容。跨平台门（3.11 / 3.12 / Linux / Windows / stable-mihomo）与真实内核集成未派发。
- 未访问真实 Cloudflare / KV，未执行发布、回滚、清理或 retention。O6 尚未在真实 CI 上产生一次“失败仍保留报告”的实例；`@actions/glob` 语义由本机探针验证，不在 CI 内重跑该探针。
- V01 的真实读一致性、传播延迟与生产故障注入仍未验证，维持独立开放状态；`not_committed` 在诊断中不再作为可接受恢复状态，属本地语义调整，不代表生产事故面已关闭。
- O5 的 `pip check` 判定仍限于 `ci.yml`；四份生产工作流缺 `pip check` 的加固余量保持开放。

## 2026-10-17：第二轮实施后独立复审

输入仍为 HEAD `cd9c1788db110d1644d47f9c22f22b546f6c2524` 加未提交实现、测试、工作流与文档；以最新实际文件核对上一轮 V01 / O6 / R12 / O5。读取 status/diff 后保护已有修改，本轮仅更新验收目录三份文档，未修改业务或测试，未提交、推送或访问生产服务。

### 实际检查

| 命令 | 实际结果 |
| --- | --- |
| `python -m pytest -m "not integration" --durations=6 -q` | exit 0；1605 passed、50 deselected；53.13 秒，最慢单项 1.57 秒 |
| `python -m ruff check .` / `python -m ruff format --check .` | 两项分别输出 All checks passed / 359 files already formatted；同一调用整体 exit 0 |
| `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；全部子门通过，仓库安全检查 475 文件；ACL4SSR upstream_reference_verified=false |
| 独立 CI 审查：`python -m pytest tests/test_ci_report_retention.py tests/test_supply_chain_audit.py -q -p no:cacheprovider` | exit 0；75 passed（与全套重叠，不累加） |
| `python .work/acceptance-20261017-review/review_probes.py` | exit 0；实际复现 V01 公开结果与 R12 反向枚举失败；捕获预期 AssertionError 后输出聚合证据 |

基线 stdout 依据本轮工具输出，未另存完整日志。反例脚本及聚合结果保存在忽略目录 `.work/acceptance-20261017-review/review_probes.py` 和 `probe-results.json`，只使用本地 fake、临时目录与合成数据。未重采 coverage，也未执行 50 项 integration、远程 CI、真实 KV 或跨平台矩阵。

### 已闭环的本地子项

- **O6 PASS：** integration 成功/失败上传步骤显式 `include-hidden-files: true`，具体 XML 白名单保持，前一轮 glob 过滤条件已经消除。配置与匹配契约通过；真实 artifact 上传仍未验证。
- **O5 判定粒度 PASS：** 按每个 job / 解释器收集 install/check，其他 job 的检查不再代偿；逐项删除的负向测试通过。四份生产工作流的额外 `pip check` 加固仍是原有独立余量，不新增为本轮故障。
- **V01 保留子项 PASS：** 新生命周期只对 `committed` 跳过保留，其他观察保存精确候选/前值；run 测试实际经过异常处理、保留与 finally 清理。旧的恢复副本丢弃路径已得到修复，本轮未发现新的生产实现回归。

### 尚未闭环

**R12 / P2：新增 run 用例再次遗漏排序。** `tests/test_stale_read_reconciliation.py:489` 仍使用 `[entry.name for entry in copies[0].iterdir()]` 与固定字母序列表比较。反例只替换 `Path.iterdir` 为同一内容的合法反向字母序，调用真实 `test_run_keeps_the_recovery_copy_and_cleans_the_private_directory`，在该断言抛 AssertionError。旧 `_copy_names()` 与旧 helper 的回归已修复，但没有保护新增 run 用例。尚未在真实 Linux 文件系统实跑失败，不能用 Windows 本轮通过宣称顺序无关。

**V01 / P1 条件性风险：公开接口仍有本地语义剩余项。** 真实发布状态机 + LaggingKV 的第二次发布轨迹中，activation 已写入而 production 读仍旧，只让 pending transaction 不可见，真实 reconciliation 输出：

```json
{"status":"not_committed","production_changed":false,"requires_manual_action":false,"actual_storage_is_candidate":true}
```

前三个字段来自返回对象，最后一个是反例对远端模型真值的独立断言。入口为 `release_reconciliation.py:45、186–194`；application/CLI 原样输出。生命周期虽然已绕开该结论并保留副本，API 本身仍没有表达观察新鲜度或需要复核。`docs/publishing.md:143` 的确定性描述与 `:145` 的新鲜度条件并列，消费者缺少机器可判定的界限。因此这是原 V01 未完成的本地接口问题，非新增回归，也不只是等待真实 REST 验证。真实服务是否产生该视图依然未实证。

### 交接

R12 重新打开；V01 拆分为“恢复副本保留已通过 / 公开接口语义仍开放 / 真实服务证据未执行”。README 当前状态与 PLAN §0.1b 已同步。复审未实施任何修复，既有代码差异保持原样。

## 2026-10-17：R12 / V01 实施窗口

输入为 HEAD `cd9c1788db110d1644d47f9c22f22b546f6c2524` 加既有未提交实现、测试、工作流与文档；本窗口只处理 PLAN §0.1b 的两项（R12 与 V01 公开接口语义）。没有访问真实 Cloudflare / KV，没有派发远程工作流，没有执行发布、回滚、清理或 retention，也没有提交或推送。证据目录 `.work/acceptance-20261017-impl/`（含 `gate-run.txt` 完整转录、两个反例变体与原始文件副本）。

### 逐项：反例 → 改动 → 定向回归

| 项目 | 反例（变体上实际失败） | 改动 | 定向回归（修复后） |
| --- | --- | --- | --- |
| R12 | `r12-unsorted-run-test.py`：把 run 断言换回 `[entry.name for entry in run_copy.iterdir()]` 字面量比较，在反向枚举注入下运行同一条新回归 → 1 failed（`At index 0 diff: 'previous.yaml' != 'candidate.yaml'`） | run 用例改用 `_copy_names()` 排序比较；`test_recovery_listing_does_not_depend_on_enumeration_order` 重写为三条轨迹（生命周期 helper、再次生命周期、真实 `run()` 的恢复目录），全部在 `Path.iterdir` 反向枚举下执行 | `pytest tests/test_stale_read_reconciliation.py -q` → 16 passed |
| V01 | `v01-pre-fix-reconciliation.py`：把陈旧读分支的状态改回 `NOT_COMMITTED` → 6 failed（`test_observation_path_that_never_saw_the_attempt_is_indeterminate`、`test_hiding_only_the_pending_intent_reports_indeterminate_for_a_live_candidate`、`test_an_empty_first_release_view_is_indeterminate_for_a_live_candidate`、`test_definite_rejection_stays_indeterminate_for_the_untouched_client_key`、`test_lifecycle_keeps_the_only_private_copy_when_the_read_path_lagged`、`test_run_keeps_the_recovery_copy_and_cleans_the_private_directory`） | `ReconciliationStatus` 新增 `INDETERMINATE`；陈旧读分支无新鲜度证据时输出 `indeterminate`，有证据时输出 `not_committed`；结果新增 `observation_freshness` 字段，`requires_manual_action` 覆盖 `indeterminate` 与 `unknown` | 同上 16 passed；`tests/test_release_reconciliation.py` 7 passed |

两个变体由 `.work/acceptance-20261017-impl/build_negative_controls.py` 从当前文件按行构造，运行后均已用原始副本还原；还原后 `sha256sum` 为 `2552734ca2cd60d82d998b7a3184c2897f58bf054cfcd459f895cedd17e480e2`（测试）与 `c05e3720b82421aceb09e310fc25c1a250626d8f49df9bc44136a45b7f920535`（实现），与运行前记录一致。

### R12（P2）：恢复清单回归覆盖真实 `run()` 轨迹

- `tests/test_stale_read_reconciliation.py:571` 的 run 用例改为 `assert _copy_names(run_copy) == _copy_names(ascending[0])`；`_run_stale_view_directory()` 抽出 run 轨迹并返回恢复目录，供主用例与反向枚举用例共用。
- 反向枚举用例先在默认枚举下跑一条生命周期轨迹，再注入 `Path.iterdir` 反向排序，分别跑生命周期与真实 `run()` 两条新轨迹；三条轨迹的副本名集合必须相同，且默认枚举下等于 `["candidate.yaml", "metadata.json", "previous.yaml"]`。生产端文件创建顺序未改动。
- 断言与顺序无关的性质由本机注入证明；Linux/Windows 门本窗口未派发，不写成已跨平台验证。

### V01（P1）：公开对账接口区分“观察到旧值”与“证实未提交”

- **`src/clash_relay/release_reconciliation.py`**：枚举新增 `INDETERMINATE = "indeterminate"`（`:31`）；`ReleaseReconciliation` 新增 `observation_freshness` 字段（`:53`）并在 `to_dict()` 输出（`:64`）；`requires_manual_action` 现在对 `indeterminate` 与 `unknown` 均为 true（`:66`）。陈旧读判定分支（`:214–233`）在无新鲜度证据时返回 `indeterminate`（`observation_freshness="unsupported"`），有证据时返回 `not_committed`（`observation_freshness="fresh"`），两者 `reason` 同为 `pre_attempt_production_still_live`、`production_changed=false`。签名新增仅关键字参数 `fresh_read_evidence: bool = False`（`:96`），默认不假设新鲜度。
- **`src/clash_relay/production_application.py`**：`reconcile_production_release`（`:408`）与 `reconcile_production_release_ids`（`:466`）透传 `fresh_read_evidence`；两个入口的 docstring 说明该断言含义。
- **`src/clash_relay/cli.py`**：`reconcile-release` 新增 `--observed-after-attempt`，`_command_reconcile_release` 把它作为 `fresh_read_evidence` 传给两条路径（`:221`、`:230`）。
- **语义边界**：已提交（`committed`）与收敛路径不变；明确拒绝场景下旧值不再被无条件写成确定未提交——生命周期内该场景的判定与候选内容无关，`indeterminate` 是其保守结论，`--observed-after-attempt` 给出日期证据后仍可判 `not_committed`（`test_definite_rejection_stays_indeterminate_for_the_untouched_client_key` 两条断言）。生命周期 `_preserve_ambiguous_release()` 未改动：它只对 `committed` 提前返回，`indeterminate` 与 `unknown` 一样进入保留路径，恢复元数据记录 `reconciliation_status`。
- **公开接口验收**（PLAN §0.1b 要求的入口）：`tests/test_production_application_unit.py` 的 `test_release_reconciliation_reports_an_undatable_old_value_as_indeterminate` 用 fake 读视图驱动真实 application 入口——远端 production 仍是旧值、无 pending intent，无参数调用得到 `indeterminate / requires_manual_action=true / observation_freshness="unsupported"`，`fresh_read_evidence=True` 得到 `not_committed / observation_freshness="fresh"`；`tests/test_cli.py` 的 `test_reconcile_release_command_forwards_the_dated_read_assertion` 断言 CLI 标志确实透传。
- **API 兼容性**：新增字段与新增仅关键字参数为向后兼容；此前消费者看到的陈旧读 `not_committed` 现在变为 `indeterminate`，属本次收紧的有意行为变更，已写入 `CHANGELOG.md` 的 Unreleased 段与 `docs/publishing.md`。
- **仍未验证**：真实 REST 读一致性、传播延迟、事务顺序约束与生产故障注入。本窗口只证明“公开接口在该输入下不再给出无需复核的确定未提交”，不写为生产事故面已关闭。

### 文档同步

- `docs/publishing.md`：命令输出改为四态（`committed` / `not_committed` / `indeterminate` / `unknown`）；`indeterminate` 的含义、`--observed-after-attempt` 的使用条件、`observation_freshness` 取值与 `requires_manual_action` 范围一并写明。
- `docs/production-cutover.md`：停止条件与后续比较从 `unknown` 扩为 `unknown` 或 `indeterminate`；补充重复对账与 `--observed-after-attempt` 的操作顺序。
- `CHANGELOG.md`：Unreleased / Changed 记录行为变更与消费者注意点。

### §0.9 实施完成门：实际命令与退出码

全部在工作区内容上运行，完整转录 `gate-run.txt`：

| # | 命令 | 退出码 | 范围与结果 |
| --- | --- | --- | --- |
| 1 | `python -m ruff check .` | 0 | All checks passed! |
| 2 | `python -m ruff format --check .` | 0 | 359 files already formatted |
| 3 | `python scripts/run_project_checks.py typecheck` | 0 | Success: no issues found in 58 source files |
| 4 | `python -m pytest -m "not integration" -q -p no:cacheprovider --basetemp=.work/acceptance-20261017-impl/basetemp-gate --cov=clash_relay --cov-fail-under=75 --cov-report=term-missing --cov-report=xml:.work/acceptance-20261017-impl/coverage-3.13-local.xml --junitxml=.work/acceptance-20261017-impl/unit-3.13-local.xml` | 0 | 1609 passed、50 deselected、77.34 秒；TOTAL 84%（要求 75%）；XML 与 JUnit 写入证据目录 |
| 5 | `python scripts/audit_risk_coverage.py .work/acceptance-20261017-impl/coverage-3.13-local.xml` | 0 | 24 个高风险模块全部达标，`release_reconciliation.py` 92.65%（门 90%）、`production_lifecycle.py` 79.29%（门 70%）、`production_application.py` 80.20%（门 75%）；risk-based coverage ratchet: passed |
| 6 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | 0 | 全部审计子门通过（含文档契约），仓库安全检查 475 文件；ACL4SSR `upstream_reference_verified=false` |
| 7 | `python -m clash_relay.cli doctor --public-only` | 0 | status=passed |
| 8 | `python scripts/repository_audit.py` | 0 | repository safety audit passed (475 files checked) |
| 9 | `python scripts/audit_supply_chain.py` | 0 | supply-chain audit: passed |

用例数由上一窗口的 1605 增至 1609（+4）：`test_stale_read_reconciliation.py` 14 → 16（新增 `test_dated_observation_of_the_same_state_is_a_conclusive_non_commit`、`test_a_visible_pending_transaction_stays_unknown_even_with_dated_evidence`，并改写既有陈旧视图用例的期望），`test_release_reconciliation.py` 6 → 7，`test_production_application_unit.py` 14 → 15，`test_cli.py` 17 → 18。

### 本窗口改动文件

- 实现：`src/clash_relay/release_reconciliation.py`（`indeterminate` 状态、`fresh_read_evidence`、`observation_freshness`）、`src/clash_relay/production_application.py`（两个公开入口透传）、`src/clash_relay/cli.py`（`--observed-after-attempt`）。
- 测试：`tests/test_stale_read_reconciliation.py`（R12 三条轨迹回归 + V01 公开语义与日期证据对照）、`tests/test_release_reconciliation.py`（默认与日期证据两条路径）、`tests/test_production_application_unit.py`（公开入口验收）、`tests/test_cli.py`（标志透传与默认值）。
- 文档：`docs/publishing.md`、`docs/production-cutover.md`、`CHANGELOG.md`。

### 边界与未执行项

- 50 项 `integration` 用例本窗口未运行（`-m "not integration"` 之外），既不记为通过也不记为失败。
- 改动仍未提交、未推送；跨平台门（3.11 / 3.12 / Linux / Windows / stable-mihomo）与真实内核集成未派发，远程 CI 最新记录不代表本工作区内容。
- 未访问真实 Cloudflare / KV，未执行发布、回滚、清理或 retention；V01 的真实读一致性、传播延迟与生产故障注入仍是独立证据缺口。
- 反例变体只用于证明回归判别力，不是长期仓库产物；`r12-unsorted-run-test.py`、`v01-pre-fix-reconciliation.py`、原始文件副本与哈希记录均留在忽略目录 `.work/acceptance-20261017-impl/`。

本节的最后一次文字修订完成后，在同一最终树（代码、测试、工作流自 run A 起未变，仅文档与 `CHANGELOG.md` 有改动）重跑同一九门脚本，完整转录 `gate-run-final.txt`：9 行仍全部退出码 0，1609 passed、50 deselected、77.11 秒，覆盖率 84%，高风险模块 24/24（逐项比例与 run A 一致），文档契约、架构契约、SLA / SLO、客户端 / DNS、服务资格、v2 发布、供应链、仓库安全（475 文件）各项审计均通过，doctor `passed`。两次运行的用例数与覆盖率相同，墙钟时间 77.34 秒 / 77.11 秒属环境与负载波动。

## 2026-10-17：第三轮实施后复审

复审输入仍为 `cd9c178` 加未提交工作区改动，重点核对最新 R12 / V01，未修改代码或测试、未执行外部写入。本轮仅追加/更新验收三份文档；旧证据目录保持不变。

| 检查 | 结果 |
| --- | --- |
| `python -m pytest -m "not integration" --durations=5 -q` | exit 0；1609 passed、50 deselected；50.00 秒 |
| Ruff check / format --check | All checks passed / 359 files already formatted；整体 exit 0 |
| `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；全部子门通过，475 文件检查；上游在线核对 false |
| `python .work/acceptance-20261017-review3/review_probes.py` | exit 0；R12 反向枚举 passed；V01 结果如下 |

```json
{"status":"indeterminate","production_changed":false,"requires_manual_action":true,"actual_storage_is_candidate":true}
```

最后一个字段来自 fake 存储真值，其余来自真实 reconciler。脚本与 `probe-results.json` 保存在新的忽略目录 `.work/acceptance-20261017-review3/`；此前复审证据未覆盖。静态/全量检查依据本轮工具输出，未另存完整日志；本轮未重采 coverage、运行真实内核集成或远程 CI/真实 KV。

**结论：** R12 已闭环，新增 run 用例反向枚举实测通过。V01 的默认状态、人工复核要求、CLI 参数和恢复保留修复有效；仅 `release_reconciliation.py:217` 的 indeterminate 分支仍把 `production_changed` 填为 false，和该分支不确定的结论不一致，应使用已有 `"unknown"` 值。该字段通过公开 application/CLI 原样返回；当前测试中数处显式断言 false，因此全套通过未发现此缺口。未发现内部自动发布路径根据此字段继续写入的证据，影响限于公开结果准确性，不扩大为已证实生产事故；将其列为原 V01 的 P2 剩余项，步骤见 PLAN §0.1c。

## 2026-10-17：第三轮实施窗口 V01 字段一致性

输入：`cd9c178` 加未提交工作区改动。本窗口只处理 PLAN §0.1c 的 P2 项：不确定分支的结果字段与结论不一致。未访问真实 Cloudflare / KV，未执行发布、回滚、清理或 retention，未提交、未推送。

### 逐项：反例 → 改动 → 定向回归

**V01 P2：`indeterminate` 分支不得同时声称“生产未改变”。**

- **反例（改实现前）：** 用二进制替换把 `src/clash_relay/release_reconciliation.py:217` 的 `production_changed="unknown"` 还原为 `production_changed=False`，其余字节不动；变体 `r13-pre-fix-reconciliation.py` 的 sha256 为 `c05e3720b82421aceb09e310fc25c1a250626d8f49df9bc44136a45b7f920535`，与上一窗口结束时记录的源文件副本 `original-reconciliation.py` 逐字节相同（该变体即修复前状态本身，非近似还原）。定向运行六个断言不确定结果的用例与一个日期证据用例：`6 failed, 1 passed`。失败：`test_reconcile_proves_candidate_not_committed_only_on_a_dated_read`、`test_definite_rejection_stays_indeterminate_for_the_untouched_client_key`、`test_observation_path_that_never_saw_the_attempt_is_indeterminate`、`test_hiding_only_the_pending_intent_reports_indeterminate_for_a_live_candidate`、`test_an_empty_first_release_view_is_indeterminate_for_a_live_candidate`、`test_release_reconciliation_reports_an_undatable_old_value_as_indeterminate`；通过的正是日期证据用例（`not_committed / false`），说明该反例只打击不确定分支的字段一致性，不影响已证实结论。恢复后源文件 sha256 回到 `d9a8bd23c365789f56c744450abe845ff9931e459a8a57d5ddb4aa5e1b27d2cf`，与改动前逐字节相同（`restored_byte_identical: true`）。
- **改动：** 仅该分支改用已有三态值 `production_changed="unknown"`。有新鲜度证据的 `not_committed`、`committed`、候选原本已在线的 `production_changed=False` 与其余 `unknown` 分支取值不变，未新增 API 或状态。
- **测试同步：** `tests/test_release_reconciliation.py` 默认路径断言 `indeterminate + "unknown" + observation_freshness="unsupported" + requires_manual_action=true`，同一状态在日期证据下断言 `not_committed + false + "fresh"`；`tests/test_stale_read_reconciliation.py` 三处不确定断言（确定拒绝、只隐藏 pending intent、空的首发视图）改为 `"unknown"`，确定拒绝用例新增日期证据后仍为 `false` 的对照，三处都保留“实际存储已是候选 / 仍为旧值”的独立断言；`tests/test_production_application_unit.py` 公开入口断言 undated `"unknown"`、dated `false`，docstring 说明未定日期的观察不得声称生产未改变。
- **定向回归：** `python -m pytest tests/test_stale_read_reconciliation.py tests/test_release_reconciliation.py tests/test_production_application_unit.py tests/test_cli.py tests/test_release_transaction_recovery.py tests/test_production_metrics.py -q -p no:cacheprovider --basetemp=.work/tmp-c1-targeted` → exit 0，92 passed。
- **公开路径探针：** `.work/acceptance-20261017-impl/c1_probes.py`（只读合成 fake；`publish()` 被调用即断言失败；工程 fixture 复用公开 `reconcile_production_release`）。输出 `c1-probe-results.json`：

```json
{
  "subsequent_release_stale_view_undated": {"status": "indeterminate", "production_changed": "unknown", "observation_freshness": "unsupported", "requires_manual_action": true},
  "first_release_stale_view_undated": {"status": "indeterminate", "production_changed": "unknown", "observation_freshness": "unsupported", "requires_manual_action": true},
  "subsequent_release_stale_view_dated": {"status": "not_committed", "production_changed": false, "observation_freshness": "fresh", "requires_manual_action": false},
  "first_release_stale_view_dated": {"status": "not_committed", "production_changed": false, "observation_freshness": "fresh", "requires_manual_action": false},
  "conclusive_commit_still_reported": {"status": "committed", "production_changed": true, "observation_freshness": null, "requires_manual_action": false},
  "conclusive_commit_reason": "release_transaction_fully_visible",
  "run_recovery_copy_reverse_enumeration": "passed"
}
```

首次与后续发布的陈旧视图都给出 `indeterminate / "unknown" / true`；日期证据路径保持 `not_committed / false`；已证实提交不受影响。同一脚本重跑真实 `run()` 恢复目录的反向枚举轨迹为 passed，R12 结论未回退。

### §0.9 实施完成门：实际命令与退出码

同一脚本 `.work/acceptance-20261017-impl/gate-run-c1.sh`（与上一窗口相同九门，仅替换日志与报告文件名），完整转录 `gate-run-c1.txt`：

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `python -m ruff check .` | exit 0；All checks passed |
| 2 | `python -m ruff format --check .` | exit 0；359 files already formatted |
| 3 | `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无问题 |
| 4 | `python -m pytest -m "not integration" -q --cov=clash_relay --cov-fail-under=75` | exit 0；1609 passed、50 deselected、77.04 秒；总覆盖率 84.32%（15275 行，未覆盖 2395） |
| 5 | `python scripts/audit_risk_coverage.py coverage-3.13-local-c1.xml` | exit 0；24 个高风险模块全部达标（`release_reconciliation.py` 92.65% / 门 90.00%） |
| 6 | `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；文档契约、架构契约、运营 SLO 契约、客户端 / DNS、服务资格、v2 发布、供应链、仓库安全（475 文件）全部 passed |
| 7 | `python -m clash_relay.cli doctor --public-only` | exit 0；`"status": "passed"` |
| 8 | `python scripts/repository_audit.py` | exit 0；475 文件检查通过 |
| 9 | `python scripts/audit_supply_chain.py` | exit 0；supply-chain audit passed |

用例数与覆盖率与上一窗口相同（1609 / 84%），说明本窗口只改结果字段与对应断言，未删除或跳过用例。

### 本窗口改动文件

- 实现：`src/clash_relay/release_reconciliation.py`（第 217 行 `indeterminate` 分支的 `production_changed`）。
- 测试：`tests/test_release_reconciliation.py`、`tests/test_stale_read_reconciliation.py`、`tests/test_production_application_unit.py`。
- 文档：`docs/acceptance/PLAN.md` §0.1c 实施状态、`docs/acceptance/README.md` 状态表、`docs/publishing.md`（`production_changed` 三态说明）与 `CHANGELOG.md`（同一处补充）。
- 行尾：`tests/test_release_bundle.py`、`tests/test_ci_report_retention.py`、`tests/test_stale_read_reconciliation.py` 由 CRLF 规范化为 LF（内容不变）。

完成上述文字修订后，在同一树重跑同一九门脚本，完整转录 `gate-run-c1-final.txt`：9 行仍全部退出码 0，1609 passed、50 deselected、135.56 秒，覆盖率 84.32%、高风险模块 24/24、文档契约、架构契约、运营 SLO 契约、客户端 / DNS、服务资格、v2 发布、供应链与仓库安全（475 文件）审计均通过，doctor `passed`。用例数与覆盖率逐项与 run A 相同，墙钟时间 77.04 秒 / 135.56 秒属环境与负载波动。

### 顺带修复：工作区行尾与 `.gitattributes` 不一致

排查上一条 CRLF 事故时发现本批工作区另有三个文件是 CRLF，而仓库 `.gitattributes` 明确要求 `* text=auto eol=lf`（`tests/test_release_bundle.py` 在 `HEAD` 的 blob 为 LF，说明是本次批量改动的文本模式脚本写入所致；另两个是本批新增文件）。三者按字节把 `\r\n` 规范化为 `\n`，规范化前后 sha256 记入 `eol-normalization.json`（`tests/test_release_bundle.py` `c80799408e89… → 2033c869e307…`、`tests/test_ci_report_retention.py` `40fccf440f4a… → 324307e7dd85…`、`tests/test_stale_read_reconciliation.py` `27167264544a… → f77a825467aa…`）。改动只涉及行尾，不涉及任何代码或断言内容。

规范化后在最终树重跑同一九门脚本，完整转录 `gate-run-c1-final2.txt`：9 行仍全部退出码 0，1609 passed、50 deselected、134.30 秒，覆盖率 84.32%，高风险模块 24/24，各审计（含 475 文件仓库检查）与 doctor `passed`。此后未再改动任何工作区文件，源文件 sha256 仍为 `d9a8bd23c365789f56c744450abe845ff9931e459a8a57d5ddb4aa5e1b27d2cf`。三次全量运行的用例数与覆盖率逐项相同，说明该窗口的改动没有增删用例。

本节随后的措辞修订只涉及验收文档，不改代码与测试；修订完成后重跑会被文档内容影响的第 6、8、9 门，结果转录 `gate-run-c1-docs2.txt`。该日志是本小节结论的证据：三行退出码均为 0 时，文档契约、架构契约、运营 SLO 契约、客户端 / DNS、服务资格、v2 发布、供应链与 475 文件仓库安全检查在最终文档上成立；若有任一行非 0，则以日志中的失败原因修订文档后重跑，不保留本节结论。

### 边界与未执行项

- 50 项 `integration` 用例仍未运行，既不记为通过也不记为失败。
- 改动仍未提交、未推送；跨平台门（3.11 / 3.12 / Linux / Windows / stable-mihomo）未派发，远程 CI 最新记录不代表本工作区内容。
- 未访问真实 Cloudflare / KV：真实读一致性、传播延迟与生产故障注入仍是独立证据缺口，本文不把该字段缺陷描述为已发生生产故障。
- 反例变体只用于证明判别力，不是长期仓库产物：`r13-pre-fix-reconciliation.py`、`run_r13_negative_control.py`、`c1_probes.py`、`c1-probe-results.json`、`eol-normalization.json` 与四份 `gate-run-c1*.txt` 均留在忽略目录 `.work/acceptance-20261017-impl/`。
- 本窗口的反例脚本第一版用文本模式读写源文件，把工作区的 LF 改成 CRLF，首次记录因此出现 `restored_byte_identical: false`；改脚本为二进制读写后重跑，工作区文件恢复原字节（`d9a8bd23c365…`）并得到 `true`。同一类事故在本批另留下三个 CRLF 文件，已按 `.gitattributes` 规范化（见上一小节）。仓库当前无 CRLF 残留。

## 2026-10-17：第四轮实施后复审通过

输入为 HEAD `cd9c178` 加既有未提交工作区。核对最新字段修复、对应测试、application/CLI 传递与验收记录。源文件 SHA-256 实测为 `d9a8bd23c365789f56c744450abe845ff9931e459a8a57d5ddb4aa5e1b27d2cf`，与上一实施窗口记录一致。复审未修改实现、测试、工作流或依赖，仅更新验收三份文档。

| 本轮实际检查 | 结果 |
| --- | --- |
| `python -m pytest tests/test_stale_read_reconciliation.py tests/test_release_reconciliation.py tests/test_production_application_unit.py tests/test_cli.py tests/test_release_transaction_recovery.py tests/test_production_metrics.py -q -p no:cacheprovider` | exit 0；92 passed，6.91 秒 |
| Ruff check / format --check | All checks passed / 359 files already formatted；整体 exit 0 |
| `python scripts/run_project_checks.py typecheck` | exit 0；58 source files 无错误 |
| `python scripts/run_project_checks.py audit --offline-acl4ssr` | exit 0；全部子门通过，475 文件检查；ACL4SSR 在线核对仍为 false |
| 原反例重放、源文件 hash 与 `git diff --check` | exit 0；反例结果如下，diff 无诊断 |

```json
{"V01":{"status":"indeterminate","production_changed":"unknown","requires_manual_action":true,"actual_storage_is_candidate":true},"R12":"passed"}
```

脚本从此前只读探针复制到新目录 `.work/acceptance-20261017-review4/`，输出保存在 `probe-results.json`，未覆盖历史证据。V01 在实际存储已为候选、读视图仍旧时保留不确定结论；R12 在合法反向枚举下通过。日期证据下的 `not_committed / false`、已提交路径及恢复副本保留由相关回归覆盖，未见其他重要回归。

**复审结论：PASS（本地整改范围），§0.1c 的最后一个字段问题已关闭。** 本轮根据单分支修复选择定向回归，未重复运行实施窗口已执行的 1609 项全量测试与 coverage；不把历史全量结果冒充本轮执行。远程 CI、跨平台/真实内核、真实 KV 一致性及生产演练仍未执行，不属于本轮本地 PASS 的证明范围。O5 四份生产工作流的额外 `pip check` 保持原有独立优化待办，未发现相关现存故障。

## 2026-10-17：推送、远程 CI 与 Windows 门修复

输入为 HEAD `cd9c178` 加全部未提交工作区。按用户指令提交并推送 `main`，随后按实际 CI 结果修复新增 Windows 门暴露的一处真实缺陷。未访问真实 Cloudflare / KV，未执行发布、回滚或 retention；push 事件下 `deploy` 被 `github.event_name != 'push'` 排除。

### 提交与推送

| 提交 | 内容 | 结果 |
| --- | --- | --- |
| `a3b718b` | R07–R12 / V01 / O4–O6 实现、测试与文档（43 文件，3843 insertions / 98 deletions） | 推送成功；CI run `38948496539` **failure** |
| `32ad94b` | Windows 门改用自有虚拟环境（3 文件，24 insertions / 8 deletions） | 推送成功；CI run `38952400087` **success** |

推送前检查：`git diff --check` 无诊断、无 CRLF 残留、差异内无凭据形态内容（仅固定 action SHA 与文档文本）、无 `.work` 产物入库。`publish.yml` 的 `deploy` 需 `github.event_name != 'push'`，push 仅跑验证。

### 首次推送暴露的缺陷（Windows 门）

- run `38948496539`：3.11 / 3.12 / 3.13、deterministic-generation、routing-required、stable-mihomo v1.19.29 与 v1.19.30 均 success，**Windows platform gate failure**，`validated` 与 `deploy` 因此 skipped。
- 失败步骤为本批新增的 `Verify installed dependency consistency`，根因：`windows-latest` 镜像把自身工具装进 `actions/setup-python` 暴露的同一解释器，其中 `pipx 1.17.12` 要求 `packaging>=26.3`，而哈希锁固定 `packaging==25.0`。安装锁会降级该 `packaging`，于是 `pip check` 报告一条本项目并不拥有的冲突。其余 6 个 job 的共享解释器未被同样污染，故只有 Windows 失败。
- 这是 `a3b718b` 引入的真实缺陷，不是偶发门：此前的安装步骤本就在改写共享解释器，只是新增的检查让它可见。
- 修复：`windows-platform` 建立并全程使用 `.work/dev-venv`（venv 创建、锁安装、可编辑安装、`pip check`、类型门、Mihomo 进程测试），判定范围限定为该 job 自己的环境，与 O5 的「谁安装谁检查」原则一致。
- 本机复现与验证（Windows，Python 3.12.15）：完整 job 序列在新建 venv 内逐条 exit 0；`pip check` 输出 `No broken requirements found.`，类型门 `Success: no issues found in 58 source files`，Mihomo 进程测试 9 passed。

### 远程 CI 最终结果（run `38952400087`，SHA `32ad94b`）

| Job | 结果 |
| --- | --- |
| Python 3.11 / 3.12 / 3.13 compatibility 与 quality | success |
| Windows platform gate | success（修复确认） |
| Verify finalized Routing V2 graph | success |
| Deterministic fictional generation | success |
| Mihomo v1.19.29 / v1.19.30 | success |
| Validate exact commit / Validated SHA | success；`validated` SHA = `32ad94b` |
| Run canonical production lifecycle | skipped（push 事件按设计不部署） |

### O6 报告留存的真实 artifact 验证

run `38952400087` 实测 6 个 artifact，全部非空且未过期：`test-reports-3.12`（62913 bytes）、`test-reports-3.11`（30724）、`test-reports-3.13`（30721）、`test-reports-windows-3.12`（532）、`test-reports-integration-v1.19.29`（2046）、`test-reports-integration-v1.19.30`（2053）。下载核对：Windows 归档仅含 `unit-windows.xml`（`errors="0" failures="0" tests="9"`）；quality 归档仅含 `unit-3.12.xml` 与 `coverage-3.12.xml`，无配置、订阅、fixture secret 或其他 `.work` 内容。这同时证明 `include-hidden-files: true` 是必要的——没有它，指向 `.work/` 的通配声明会静默归档 0 个文件。

### 证据边界

- 本轮未运行 50 项 `integration` 用例；其通过由上述远程 job 记录，不并入本地计数。
- 未访问真实 Cloudflare / KV：V01 的真实读一致性与传播延迟、生产故障注入仍是独立缺口，本次未验证。
- `deploy` 未触发，生产发布、回滚与 retention 未验收。
- 本机 pytest 默认临时根目录 `%TEMP%\pytest-of-Healer` 在本会话环境中不可访问，故本地全量运行使用 `--basetemp=.work/...`；该目录属忽略范围。首次未加该参数时出现的 326 个 setup error 全部是这一环境问题（`PermissionError: [WinError 5]`），非代码失败，与文档记载的 1609 项一致。
