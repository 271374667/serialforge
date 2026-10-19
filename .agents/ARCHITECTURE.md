# serialforge · 架构总览

> 类型: 活文档（原地更新，不编号、不归档）  |  最后更新: 2026-10-19
> 基线 commit: d9e6ed4（扫描/连接同步异步拆分与探测加固）  |  适用版本: 0.0.1
> 形态: 完整  |  维护: AI 更新（见 `.agents/rules/15_architecture_doc.md`）
> 读法: 第 1、2、3、6 节为必读区（每次会话读，合计 ≤150 行）；其余按需读

## 1. 这个项目是干什么的（必读）

- **一句话**：serialforge 为 Windows x64 应用提供基于 QtCore 信号槽和线程的串口通讯、设备发现与命令调度能力。
- **背景与动因**：把串口字节流、帧定界、设备探测、命令响应和日志封装为可发布到 PyPI 的库，让 Qt6 应用只接触稳定的门面 API。
- **明确不做**：不包含任何具体设备业务命令；不使用 asyncio 或多进程；不导入 QtWidgets/QtGui；不承诺 Linux、32 位 Windows 或硬件无关的真机行为。

## 2. 外部形态（必读）

- 交付物：可构建的 Python 库 `serialforge` 与本地 CI 脚本；无硬件 Demo 放在 `examples/`，测试替身放在 `tests/support/`，不进入发布 wheel。
- 运行方式：使用方 `from serialforge import ...`；业务命令运行时声明并注册；Qt 应用提供 `QCoreApplication` 事件循环。
- 关键依赖：Python >=3.11；`pyserial>=3.5`、`loguru>=0.7`；QtCore extras 选择 `PySide6-Essentials>=6.11.2`、`PyQt6>=6.11` 或 `PyQt5>=5.15.11`；uv + hatchling 构建。Qt 下限取本次完整套件通过的保守值；旧版 Qt 绑定存在业务 `connect` 名称冲突，新门面已改用 connect_sync/connect_async，兼容 `SerialHandler.connect` 仍保留旧名。
- 运行接口遵循知识索引 0005；统一 Spec/Message、主线程期限回调与 SerialForge 门面已接入。qt_core 只导入选定绑定的 QtCore；QtPy 因 QtGui 导入路径不采用，三绑定 Python 3.11–3.14 十二个组合均已实测通过。

## 3. 模块地图（必读）

| 模块 / 目录 | 职责（一句话） | 入口（文件:类） | 对外提供 | 依赖谁 | 被谁依赖 |
| --- | --- | --- | --- | --- | --- |
| `src/serialforge/enums.py` | 稳定枚举 | 定义层 | 顶层与 advanced 枚举 | 标准库 | 所有层 |
| `src/serialforge/models.py` | 不可变声明、结果与配置数据类 | 定义层 | 顶层日常数据类 | enums/errors/advanced 类型约束 | 所有层 |
| `src/serialforge/spec.py` | 统一不可变声明（请求/事件/NO_REPLY） | `spec.py:Spec` | 顶层 `Spec` | models/enums/errors | 门面、注册与解析 |
| `src/serialforge/message.py` | 统一不可变结果消息 | `message.py:Message` | 顶层 `Message` | enums/models | 门面、调用句柄与观察信号 |
| `src/serialforge/qt_core.py` | 选定 Qt 绑定的 QtCore 再导出（窄适配） | `qt_core.py` | 库内 QtCore 符号 | 选定绑定 | 所有实现层 |
| `src/serialforge/errors.py` | 异常层次 | 定义层 | `errors` 命名空间 | 标准库 | 所有层 |
| `src/serialforge/settings.py` | 默认配置常量 | 定义层 | 内部默认值 | 标准库 | 定义层与实现层 |
| `src/serialforge/protocols.py` | 后端与传输协议 | 定义层 | 内部协议 | models | transport/discovery/connection |
| `src/serialforge/advanced.py` | 进阶配置和统计再导出 | 再出口 | `advanced.__all__` | 定义层 | 使用方与实现层 |
| `src/serialforge/transport/` | 字节流、定界、校验、延迟与端口后端 | `__init__.py`：传输组件再导出 | 内部模块 | L0 | discovery/connection |
| `src/serialforge/connection/` | 命令注册、响应解析、调度与连接生命周期 | `serial_forge.py:SerialForge`、`serial_handler.py:SerialHandler`（兼容）、`command_registry.py:CommandRegistry`、`command_dispatcher.py:CommandDispatcher`、`handler_core.py:HandlerCore`、`command_call.py:CommandCall`、`scan_call.py:ScanCall`、`connect_call.py:ConnectCall`、`connection_worker.py:ConnectionWorker` | 顶层门面与内部调度组件 | L0-L2 | `__init__.py` |
| `src/serialforge/discovery/` | 阻塞扫描、内部多线程波特率探测与缓存 | `device_finder.py:DeviceFinder`、`port_scanner.py:PortScanner`、`baud_prober.py:BaudProber` | `DeviceFinder` | L0-L1 | connection/顶层 |
| `src/serialforge/diagnostics/` | 流量与文件日志 | `traffic_logger.py:TrafficLogger`、`log_file_manager.py:LogFileManager` | 内部诊断组件 | L0-L1 | connection |
| `tests/support/` | 可脚本化假后端与模拟设备 | `__init__.py`：本地测试替身 | 测试内部导入 | L0-L1 | tests/、examples/ |
| `tests/` | 契约、守卫、单元和 API 快照 | `test_*.py` | 本地验证 | src | CI |
| `examples/`、`scripts/` | 外置 Demo 与本地 CI/发布/真机验收脚本 | `ci.py`、`release.py`、`measure_windows_timer.py`、`update_api_snapshot.py` | 开发与维护者工具，不发布测试替身 | 源码或安装的 wheel、tests/support | 使用方/本地验收 |
| `docs/`、`mkdocs.yml` | mkdocs-material 使用文档站点源码与配置 | `index.md`、`api.md` 等页面 | 本地构建站点（产物在 `outputs/04_docs_site/`），不随 wheel 分发 | 只写文档，不导入源码 | 使用方/维护者 |

当前实现：SerialForge 组合 HandlerCore；Spec/Message 为统一声明与消息，CommandCall 保存完成状态与期限，通过门面的 QtCore 排队桥接投递主线程闭包。所有源码 Qt 导入经 qt_core，PySide6/PyQt6/PyQt5 为安装 extras；R5 双绑定矩阵已通过，PyQt5 于 2026-10-10 加入。

## 4. 交互关系（按需）

- 依赖单向：定义层 L0 → transport/diagnostics L1 → discovery L2 → connection L3 → 顶层再导出 L4；`tests/support/` 只作为测试注入边界，不是发布层。
- 典型路径：`SerialForge.connect_sync()` 阻塞等待工作线程完成发现、打开与初始化，成功返回即可发送；`connect_async()`/`scan_async()` 在后台线程走同一路径，经 `ConnectCall`/`ScanCall` 把结果回调到主线程。`DeviceFinder.find()` 在内部线程池扫描并等待所有探测句柄关闭后返回；命令经 registry/dispatcher 匹配并以 Qt 信号发送结果。
- R1–R5 已完成：公共接口收敛到 `SerialForge` / `Spec` / `Message`，`scan_sync()` / `connect_sync()` / `send_sync()` 阻塞，异步结果经主线程闭包投递；`scan_async()` / `connect_async()` 分别返回 `ScanCall` / `ConnectCall`，以 `add_done_callback` 在主线程交付设备列表 / 已连接设备。R5 同时交付使用文档站点。无硬件示例与使用文档都只展示门面 API，兼容入口（`SerialHandler` 等）只出现在迁移章节。

## 5. 状态与外部边界（按需）

- 外部 I/O：M2 起由 pyserial 访问串口；QtCore 负责线程与信号；loguru 在 M3 接入。
- 配置：人写的工具配置保留根目录 `pyproject.toml`；运行时日志、缓存和状态按项目规则进入 `data/` 或用户目录。
- 本地 CI：默认全量检查、独立 Python 3.11–3.14 矩阵、构建/twine 与外置 wheel 验收；pytest 默认由开发专用 pytest-xdist 自动选择最多 4 个 worker（worksteal），`-n 0` 串行调试，`-m hardware` 强制串行。`--fast` 单版本，`--lowest` 在临时副本固定开发工具后解析最低运行依赖，`--smoke` 验收已构建 wheel，`--release` 覆盖全部。CI 不得改动仓库 uv.lock。
- 发布：构建物统一进入 `outputs/01_dist/`；scripts/release.py 默认 dry-run，先要求干净工作区并执行发布前 CI。维护者上传要求显式参数和交互确认，AI 不执行上传、push 或 tag。

## 6. 关键不变量（必读，最容易改错的地方）

- 用户 2026-10-08 覆盖 v10 的入口与身份必填：scan_sync/connect_sync/find 阻塞，不依赖 Qt 事件泵；VID/PID 可省略，提供时才过滤，指定端口绕过过滤；端口并行、同端口波特率串行，ALL 扫完全部，FIRST_MATCH 协作取消并回收探测句柄 —— **违反**：设备未就绪、资源泄漏或漏检目标 —— **生效范围**：models/connection/discovery/examples。
- 单个 SerialForge 每次 connect_sync/connect_async/scan_sync/scan_async 先完整 disconnect 并回收旧会话；连接同设备也重连，扫描回收后才应用可选配置并探测，结束保持断开；旧回收失败不继续，准备/执行期间互斥，独立 configure 仍要求空闲断开 —— **违反**：串口抢占、漏扫旧设备、旧调用/回调或分片跨会话污染 —— **生效范围**：SerialForge，见知识 0008（2026-10-10 用户覆盖）。
- 探测（scan_sync/scan_async 与 connect_sync/connect_async 内部发现）是查询接口：个别端口打不开、写不进、定界失败或应答超时只跳过该候选并继续其余端口，全部无命中返回 `[]`，库不把这类环境故障包装成 ProbeError；只有配置非法或并发误用才抛错 —— **违反**：一台机器的蓝牙虚拟串口就让整次扫描失败，调用方只能拿到库异常 —— **生效范围**：discovery、SerialForge、ConnectionWorker，见知识 0008（2026-10-19 用户覆盖）。
- 探测延迟必须有界：每个端口只 open 一次、其余速率在同一句柄切换；open 在可放弃辅助线程中受 `RuntimeConfig.probe_open_timeout_s` 约束，超时跳过并关闭迟到句柄；每个速率先按 `probe_activity_window_s` 观察回包字节再决定是否等满超时 —— **违反**：系统级 open 超时把一次扫描拖到十几秒，且速率数会成倍放大 —— **生效范围**：discovery、protocols/transport 及测试替身，见知识 0008（2026-10-19 用户要求）。
- 顶层导出按知识索引 0005：当前过渡 19 名字、下一轮收敛最终 11；源码与快照随阶段同步 —— **违反**：接口与验收不同步 —— **生效范围**：顶层导出与 API 测试。
- 新 Spec 与兼容 CommandSpec/EventSpec 均 frozen=True/eq=False，全链路保留对象身份 —— **违反**：result.spec is X 失效 —— **生效范围**：models、registry、dispatcher、回调/信号。
- 新门面仅 received/raw_sent/connection_state_changed，不公开 traffic_logged；主线程成功且期限内 add_done_callback，默认发送总期限 3 秒，错误从同步/调用对象读取 —— **违反**：UI 跨线程或接口误导 —— **生效范围**：R2–R5，具体见知识索引 0005；兼容 SerialHandler 也不暴露旧信号。
- received 所有载荷必须 raw_data 非空，库统一出口无 category 例外；LINE 逐完整行保留接收结束符，空 read/空行/空 raw_data 元信息不触发信号；半行按读取线程隔离并在探测切换/断开/重连时清理，结果仍经同步/调用句柄/回调获取 —— **违反**：空信号、显示分片或跨会话旧数据 —— **生效范围**：SerialForge，见知识 0007（2026-10-10 用户进一步覆盖）。
- 发送终止符与接收定界相互独立；无结束符回复使用显式正数 `silence_gap_s` 的 `SILENCE_GAP`，接收循环须在无新字节时周期性 `flush()` —— **违反**：半包误判或命令永不完成 —— **生效范围**：advanced、transport、M5 connection、M6 discovery。
- 定义层不导入实现子包，跨子包优先经 `__init__.py`；既有 connection_worker 重连身份解析直接使用 discovery.port_scanner 是当前例外 —— **违反**：循环依赖和架构守卫失败 —— **生效范围**：`src/serialforge/`。
- 库内只使用 loguru DEBUG 且默认静默 —— **违反**：宿主应用收到意外输出或等级 —— **生效范围**：diagnostics 与 connection。
- 不在源码定义业务 `CommandSpec` / `EventSpec` —— **违反**：库无法复用于不同设备 —— **生效范围**：`src/serialforge/`。
- Qt 依赖限于 QtCore，导入无线程、文件、串口和应用初始化副作用 —— **违反**：无界面导入环境被污染 —— **生效范围**：顶层导入与所有定义层模块。
- 项目包内一律使用绝对导入，测试替身只存在于 `tests/support/` —— **违反**：独立分析、源码运行与 wheel 安装的导入路径不一致，测试组件会被错误打包 —— **生效范围**：`src/serialforge/`、`tests/support/`、构建配置。
- 已知坑：当前用户级 `scaffold.py` 有冲突标记；M1 规范目录由模板手动落地，需在交付记录中保留该事实。

## 7. 深潜入口（按需）

| 想深入 | 读什么 |
| --- | --- |
| 设计契约 | 已落地契约见下方知识索引 0005；v10 方案原件不在本仓库内 |
| 使用方推荐用法（声明集中、异步闭包、scan_async/connect_async + VID/PID 加速） | `.agents/docs/knowledge/software/0011_推荐用法_集中声明与异步闭包.md` |
| 用户批准的公共 API 改造覆盖契约 | `.agents/docs/knowledge/software/0005_公共API改造_契约索引与迁移.md` |
| Spec 声明与 add 重载 | `.agents/docs/knowledge/software/0006_Spec声明与add重载契约.md` |
| Message 与收发观察 | `.agents/docs/knowledge/software/0007_Message与收发观察契约.md` |
| SerialForge 配置/扫描/生命周期 | `.agents/docs/knowledge/software/0008_SerialForge配置扫描与生命周期契约.md` |
| CommandCall 回调与异常 | `.agents/docs/knowledge/software/0009_CommandCall主线程回调与异常契约.md` |
| QtCore 双绑定适配 | `.agents/docs/knowledge/software/0010_QtCore双绑定适配决策.md` |
| 阻塞连接与可选身份的最新契约 | `.agents/docs/knowledge/software/0004_阻塞连接与可选USB身份契约.md` |
| M1 进度 | `.agents/docs/checkpoint/_archive/0001_m1-foundation.md` |
| M2 交接 | `.agents/docs/checkpoint/_archive/0002_m2-transport.md` |
| 任务队列 | `.agents/docs/todo/README.md` |
| 定义层实现 | `src/serialforge/enums.py`、`models.py`、`spec.py`、`message.py`、`errors.py`、`advanced.py` |

## 8. 变更日志（按需，倒序）

| 日期 | commit | 改了什么 |
| --- | --- | --- |
| 2026-10-19 | 本次提交 | 公开文档去模板化：删除“只做一件事/不是 A 而是 B/先放一边”等句式，改为口语化连接；快速开始不再从 `serialforge.qt_core` 导入 Qt 类，改为使用方自己的 PySide6/PyQt 绑定创建应用；`SerialForge` 在无应用实例时抛出明确 `ConfigError`，示例用 `examples/qt_app.py` 垫片兼容三套绑定，新增宿主应用与缺失应用测试 |
| 2026-10-19 | 本次提交 | 文档语言改为面向“只懂 Qt、第一次接触串口”的口语化写法：进阶页用比喻展开概念，新增 `docs/appendix/glossary.md` 术语表（顶部“附录”Tab），正文首次出现的串口/线程/协议术语链到对应锚点；README 与入门页同步 |
| 2026-10-19 | 本次提交 | 文档按难度分层并启用顶部 Tab：`新手入门`（`docs/index.md` + `docs/beginner/`）只保留最小可用路径，`进阶指南`（`docs/advanced/`）按功能拆页，示例页拆成简单/完整两页；示例目录拆为 `examples/simple/`（同步、异步各一个短脚本）与 `examples/advanced/`（原有四个完整示例），测试、CI、源码 docstring 与文档引用同步 |
| 2026-10-19 | 本次提交 | 使用方推荐口径改为 `scan_async()` + `connect_async()`（可给 `vid_pid` 在打开前过滤端口加速），并用 `send_async` 主线程闭包消费；README、`docs/` 五页、知识 0011 与示例同步。随库示例迁到 `received` 非空原始字节契约：命令结果从调用句柄/回调/同步接口取，流式与主动上报按原始行观察，`tests/test_examples.py` 断言同步 |
| 2026-10-19 | 本次提交 | 连接拆分 `connect_sync()` / `connect_async()`：后台线程走同一连接路径，`ConnectCall.add_done_callback` 在主线程交付 `DeviceInfo`，失败不回调、`result()` 重抛、`cancel()`/`disconnect()` 中止并抑制回调；异步期间拒绝并发 scan/connect/configure，示例/文档/API 快照同步 |
| 2026-10-19 | 本次提交 | 同步扫描改名 `scan()` → `scan_sync()`，与 `send_sync()` 命名一致；示例、测试、文档与 API 快照同步 |
| 2026-10-19 | 本次提交 | 新增 `scan_async()` + `advanced.ScanCall`：后台线程执行同一扫描路径，`add_done_callback` 在主线程交付 `list[DeviceInfo]`，失败不回调、`result()` 重抛，`cancel()`/`disconnect()` 抑制回调；异步期间拒绝并发 scan/connect/configure，API 快照与文档同步 |
| 2026-10-19 | 本次提交 | 探测延迟有界：每端口只 open 一次并在同一句柄切换波特率；open 移到可放弃辅助线程（`probe_open_timeout_s`，迟到句柄自动关闭并释放租约）；新增 `probe_activity_window_s` 活性窗口，无回包字节的速率提前跳过；`RuntimeConfig` 增加两个字段并同步 API 快照 |
| 2026-10-19 | 本次提交 | 探测改为逐候选跳过故障：端口打开/写入/定界/应答失败不再让整次扫描抛 ProbeError，无命中返回 `[]`；门面 `scan()` 与 `connect()` 内部发现统一该策略，只有配置错误与并发误用报错 |
| 2026-10-13 | 本次提交 | 用户确认使用方推荐用法（命令集中声明一次 + `send_async` 主线程闭包改界面 + `scan(baudrates, probe=已声明命令)` 后 `connect(DeviceInfo)`）；新增知识 0011，并据此重写 README 与 docs/ 站点（快速开始改用该形态，补齐缺失的平台页） |
| 2026-10-10 | 本次提交 | scan 统一先回收旧连接，再应用配置并探测；覆盖重复扫描连接、声明身份、取消/异常清理和准备/探测阶段并发互斥 |
| 2026-10-10 | 本次提交 | SerialForge 每次 connect 先完整关闭旧会话，同设备也重新连接；保留配置/声明/初始化序列，覆盖资源顺序、取消和失败恢复 |
| 2026-10-10 | 本次提交 | 用户明确 received 的非空过滤是库行为：统一出口只发布非空 raw_data，撤回用户 demo 修改，命令结果与回调独立保留 |
| 2026-10-10 | 本次提交 | received 的 LINE 原始接收改为非空完整行，保留接收结束符；隔离探测分片并清理会话半行，保留非行协议与命令结果通知 |
| 2026-10-10 | 本次提交 | PyQt5 绑定支持：qt_core 三绑定、pyqt5 extra、pyqt5-qt5 平台锁定、CI 三绑定十二组合通过 |
| 2026-10-09 | a17c9c3 | 归档 R1–R5 改造任务并记录双绑定验收 |
| 2026-10-09 | 本次未提交文档修订 | 新增 mkdocs-material 使用文档站点（`docs/` + `mkdocs.yml`）与 README 指南面 API；示例页改用 `scan()`/门面调用与真实输出，API 页修正属性签名，平台页修正依赖描述；`examples/` 去掉 `_handler` 旧命名、`find()` 旧描述与死代码，改用 `send_sync()` 同步取结果，并新增 `async_callbacks.py` 演示文档推荐的 `add_done_callback()` 主线程闭包与 STREAM 收尾；同步本总览模块地图与深潜入口 |
| 2026-10-09 | 89a28de | R1–R5 实现完成；八个 Python/Qt 组合各 169 通过、1 硬件跳过；干净双绑定 wheel、最低依赖、静态检查、构建与文档站点均通过；真机仍未验收 |
| 2026-10-09 | R1 契约提交 | 固化 SerialForge/Spec/Message、主线程期限回调与收发观察目标；选窄 Qt6 层；源码/依赖不变，22 项基线通过 |
| 2026-10-08 | 本次提交 | 阻塞连接/发现、移除 find_async、VID/PID 可省略与缓存回退；探测并发/模式保持原设计，125 通过、1 硬件跳过 |
| 2026-10-08 | 38d30d0 | pytest-xdist 仅作开发依赖；默认最多 4 个 worker、硬件选择强制串行；完整发布前 CI 各环境 100 通过、1 硬件跳过 |
| 2026-10-08 | 559c714 | 完整发布前 CI 通过，Python 3.11–3.14、干净 wheel、最低组合均 99 项通过；真机矩阵仍待验收 |
| 2026-10-08 | 112c954 | 最低组合 99 项通过；Qt 下限收紧至实测 6.11.2，临时副本固定开发工具后解析最低运行依赖 |
| 2026-10-08 | 7d16a4c | 补齐外置 Demo/README、独立 CI 和 release dry-run；干净 wheel 导入、Demo 及测试通过 |
| 2026-10-08 | 84fa007 | 统一绝对导入，将测试替身移至 tests/support，并禁止测试内容进入 wheel/sdist |
| 2026-10-08 | 4d253a0 | 交接核对交付边界：Demo、完整文档、Python 矩阵和干净 wheel 环境仍待 M7 |
| 2026-10-08 | e8c73ca | M5 连接工作线程、自动重连、心跳与日志会话；M6 扫描、波特率探测、缓存、取消和 COM11 真机验证完成 |
| 2026-10-08 | 526ac6f | 明确支持无结束符回复的静默定界，校验间隔并覆盖分片和动态版本号 |
| 2026-10-08 | 393c7b9 | M4 调度边界、并发写锁与流量信号背压完成；真机版本查询通过 |
| 2026-10-08 | 222044c | M4 增加注册表、响应解析、调度器及 handler 的注册发送入口 |
| 2026-10-08 | 1716749 | 同步 M3 已完成的诊断模块与阶段状态；等待确认进入 M4 |
| 2026-10-01 | 40ab7e7 | M2 传输基础设施、假后端和规范化迁移已提交 |
| 2026-09-30 | 98ba269 | 完成 M1 定义层与规范目录；进入 M2 传输层开发 |
