# 下载流量与 SUB_1 隔离计划

## 目标与验收范围

**所有进入项目下载路径的连接，最终可达来源集合中绝无 `subscription_1`（SUB_1）。** 通用 ProxyLite 和用途未知的流量使用 General，不因域名貌似网页而自动取得 SUB_1 权限。验收同时检查 listener 强绑定、UDP terminal guard、规则顺序、公开 selector、provider、dialer-proxy、最终 RuntimeGraph 和实际 Mihomo 行为。

下载路径包括下载专用入站、已在目标平台验证的下载进程、ACL4SSR `Download.list`、经确认的 Play/release/update 下载域名。

部署若要求“任意应用的任意下载绝不使用 SUB_1”，必须确保所有下载请求都进入上述受控路径。普通浏览器或 AI 的同进程、同域名、同 HTTPS 连接可能同时传输网页和文件，Mihomo 的连接级规则不能可靠识别其中的未知下载。允许此类不透明连接使用 SUB_1 的部署，**不得通过绝对下载隔离验收**。若客户端不能强制交接下载到专用入站或已验证下载器，就不得为该客户端开放 SUB_1 路径。

报告区分：

- **配置保证：** 进入下载路径的连接在配置上无法到达 SUB_1。
- **部署保证：** 客户端下载交接及入口限制已验证，实际下载均进入受控路径。只有两项都通过，才能声明该部署“下载绝不使用 SUB_1”。

## 不变量

```text
A  subscription_1.allowed_uses == {browsing, ai}
B  routing.scenarios.download.source_use == general
C  reachable_sources(下载流量, final_qualified_graph) 不含 subscription_1
D  download-in listener.proxy == 下载流量，不依赖 rule mode 或 IN-NAME 才隔离
E  每个普通入口下载 classifier 都由同一声明生成 primary + 相邻 REJECT；
   不支持 UDP 时不能继续匹配 browsing/ai/ProxyLite
F  编译、资格筛选和发布前审计失败时停止生成/发布
G  ProxyLite、MATCH 和 General 的所有可达来源均不含 subscription_1
```

`DIRECT`/`REJECT` 不使用 SUB_1；更早的 general-only 目标也可能满足隔离，但都不能报告成“进入下载 selector”。每个例外需列明首次命中、最终目标和图审计结果。如要求全部下载都代理，应另行确定；本计划的硬约束是 SUB_1 不可达。

## 项目现状与改动入口

`policies/routing.yaml` 已声明 download 使用 general。`下载自动` 和地区手动组来自 general inventory。保留 SUB_1 的 browsing/ai 权限、六个公开 selector、AI 地区策略、ACL4SSR 基线和现有 qualification pipeline。

复用 `RuntimeGraph.reachable_sources` 与 `production_audit` 的来源映射和遍历，不新建一套图算法。`doctor --public-only` 没有实际订阅节点或最终图，只负责声明级预检；最终可达性归生成和生产审计。

`rules/acl4ssr.yaml` 的 `sources` 从固定 ACL4SSR 仓库和 commit 拉取。本地新建 `rules/google_play_download.yaml` 不会自动参与生成。初期用现有 `inline_rules` 存放少量规则；若规则增长到需要独立文件，须同时扩展 schema、加载器、编译器、审计和 Drift Guard。

## 优先级与首次命中

实际规则相对顺序如下；每条下载规则后均紧邻同 classifier 的 `REJECT` guard：

```text
1    Download-only inbound        → 下载流量（listener.proxy 强绑定）
2    Verified download processes  → 下载流量

10   LocalAreaNetwork             → 原目标
20   UnBan                        → 原目标
30   BanAD                        → 原目标
50   GoogleFCM                    → 原目标
60   GoogleCN                     → DIRECT
70   SteamCN                      → DIRECT
80   Microsoft                    → 原目标
90   Apple                        → 原目标
100  Telegram                     → 消息通讯

101  Confirmed Play endpoints     → 下载流量
102  Confirmed bulk/update hosts  → 下载流量
103  ACL4SSR Download             → 下载流量
105  AI                           → 人工智能
106  OpenAI                       → 人工智能
110  ProxyMedia                   → 流媒体
120  ProxyLite                    → 代理选择 / General
130+ China rules / MATCH           → 原目标
```

入口和进程规则必须先于任何可能通往 SUB_1 的规则。Play 进程访问 `dl.google.com` 因而进入下载流量；普通进程访问同域名仍按 GoogleCN 直连。这是有意的进程级覆盖，需写入回归矩阵。ACL4SSR Download 与高置信度域名规则都必须先于 AI、ProxyMedia、ProxyLite；留在国内 DIRECT 规则之后的重叠域名要逐项列为例外并证明 SUB_1 不可达。

不得用 `DOMAIN-KEYWORD,google`，也不得把整个 `github.com`、`googleapis.com`、`cloudfront.net`、`dropbox.com` 等宽域名归类下载。普通 GitHub 页面若命中 ProxyLite 则走 General；只有确认的 release asset 主机才纳入下载分类。

规则顺序检查只是静态证据。真实 Mihomo 测试要验证入口/进程与 GoogleCN、AI、OpenAI、ProxyMedia、ProxyLite 目标域名组合的**首次命中规则及最终目标组**。

## 分类来源

### ACL4SSR Download

保留 `Download.list`，先盘点已有进程与域名规则，避免重复。它的命中结果原则上进入 `下载流量`；若更早规则命中，必须在例外矩阵中显式批准并证明该目标不含 SUB_1。任何更早的 browsing/ai 命中均为失败。

### Google Play 与下载器进程

`PROCESS-NAME,com.android.vending` 及 aria2、IDM、FDM、qBittorrent、Transmission、迅雷等候选规则，只有在目标系统、客户端接管模式和固定 Mihomo 版本中验证实际发起进程后，才能列入硬保证。Android Play 服务或外部进程、桌面进程名差异均需实测。无法验证的平台改用下载专用入站，不标记进程隔离通过。

`curl`、PowerShell、Python、Node、整个浏览器进程不默认归类下载。进程名只是分类信号；真正的来源权限由 `allowed_uses`、general inventory 和最终图审计约束。

Play 域名从 fixture 中的 `play.googleapis.com`、`android.clients.google.com`、`gvt1.com`、`gvt2.com` 逐项审查。每项需要用途证据、首次命中测试和普通 Google 网页/API 的负向测试；承载混合用途的主机不应宣称是“确定下载端点”。Bulk/update 规则同样只收录具体 hostname 或窄后缀，并记录来源、维护日期和误判风险。

## 下载专用入站

本机 download-only inbound 以 `proxy: 下载流量` 强绑定目标；`IN-NAME` 仅作兼容与回归信号。输出 schema 与生产审计必须验证绑定目标，Mihomo 固定版本还需实测 rule/global mode 下 listener 的实际行为。

入站规则仍排在首位，但隔离依赖 listener `proxy` 强绑定。普通入口的下载进程、域名和 Download ruleset 均由编译器自动追加相邻 `REJECT` terminal guard，阻断 Mihomo 在 UDP adapter 不支持时继续向下匹配。客户端能够选择普通入口时，部署验收仍需验证浏览器交接或下载器实际使用受控路径。

## 编译、审计与观测

1. Routing V2 加载时拒绝 `download.source_use != general`，并检查 SUB_1 权限恰为 `{browsing, ai}`。
2. 在最终 qualified RuntimeGraph 中，对 `下载流量`、`下载自动` 和各手动地区目标复用来源遍历：不得有未解析引用或 SUB_1，其他可达来源均须允许 general。不要断言 SUB_2～5 必须全部出现，资格筛选后它们可能为空。
3. 对生成规则序列建立分类映射审计。验证 listener 强绑定及每个下载 classifier 的 primary/REJECT 严格相邻。故意颠倒优先级、删除 guard、误接 browsing provider、修改 source_use 的负向测试必须使 CI 失败。
4. Drift Guard 声明下载扩展 ID、目标、优先级和相对基线顺序；不增加新 scenario 或公开 selector。
5. `doctor --public-only` 报告 `declaration_check`；最终生成/生产 proof 报告 `runtime_graph_check` 和实际分类器状态。未执行的平台验收标记为 `unverified`，不写 URL、hostname、节点名或订阅地址。

## 验证矩阵

| 输入 | 首次命中 / 最终目标 | SUB_1 |
|---|---|---|
| 下载专用入站 + 任意外网域名 | listener.proxy → 下载流量 | 不可达 |
| 已验证下载器 + AI/Google/媒体域名 | 进程规则 → 下载流量 | 不可达 |
| Play 实际发起进程 + `dl.google.com` | 进程规则 → 下载流量 | 不可达 |
| 普通进程 + `dl.google.com` | GoogleCN → DIRECT | 不可达 |
| ACL4SSR Download 样本 | 下载流量或已批准的 general-only/DIRECT/REJECT 目标 | 不可达 |
| 确认的 Play/bulk 下载域名 | 下载流量或已批准的 general-only/DIRECT/REJECT 目标 | 不可达 |
| 普通 Google/GitHub 网页 | ProxyLite/General 或原通用规则 | 不可达 |
| ChatGPT/Claude/Gemini 普通请求 | 人工智能 | 可达 |
| 流媒体、Telegram、MATCH | 原 general 路径 | 不可达 |
| 普通 `github.com`、`google.com`、`cloudfront.net` | 不因下载扩展整体改道；ProxyLite 用 General | 不可达 |

声明与图测试用含 SUB_1、SUB_2～5 的 fixture 编译并资格筛选。固定稳定版 `v1.19.30`、`v1.19.29` 均运行 `mihomo -t` 和真实连接测试；`mihomo -t` 仅证明配置可加载，不证明分类语义。Android/FlClash/桌面进程识别与入口交接应在实际客户端验收，CI 无法覆盖的平台必须留下人工验收记录。

## 实施顺序

1. 契约：固定权限与 general-only；扩展最终图审计及负向测试。
2. 规则：增加入口/进程/域名扩展、优先级约束与 Drift Guard；验证首次命中。
3. 入站：打通 schema、渲染、Mihomo 与客户端连接测试，作为未知浏览器下载的受控路径。
4. 进程：逐平台验证 Google Play 和下载器，验证通过后才计入硬保证。
5. 域名：逐项加入窄范围 Play/release/update 端点并完成负向回归。
6. 发布：更新 `docs/rules.md`、Routing V2 文档、doctor 和生产 proof，附客户端交接验收记录。

可拆分多个 PR，但每个 PR 都要保持生成、审计和测试一致；最终合并前运行完整稳定版 Mihomo 矩阵。

## 最终验收

1. SUB_1 只允许 browsing、ai；download scenario 固定为 general。
2. 最终图中的 `下载流量` 及自动、手动路径均无法到达 SUB_1，未解析引用直接失败。
3. download-in listener 强绑定下载流量；已承诺的下载进程在真实 Mihomo 中先于 GoogleCN、AI、OpenAI、ProxyMedia 或 ProxyLite。
4. ACL4SSR 和项目下载域名拥有相邻 terminal guard；UDP 不支持下载出口时 REJECT。普通 ProxyLite 用 General，AI 与受控网页浏览 selector 仍可合法使用 SUB_1。
5. 固定稳定版 Mihomo、Drift Guard、负向回归及生产审计全部通过。
6. 生产 proof 分列配置保证和各平台部署保证；下载交接未验证的客户端不得标为“所有下载绝不使用 SUB_1”。

保留现有 SUB_1 倍率/EMBY 过滤、SUB_2～5 权限、AI 地区策略、ACL4SSR 基线、DNS、Cloudflare KV 和 qualification pipeline；只扩展完成本契约所需的规则、入站、审计与文档。
