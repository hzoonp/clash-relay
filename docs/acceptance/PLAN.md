# clash-relay 后续统一计划

建立日期：2026-10-13。当前状态只见 [README](README.md)，实际执行与证据只追加到 [EXECUTION](EXECUTION.md)。本次编制计划，不实施业务修复。

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
