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
