# serialforge · 架构总览

> 类型: 活文档（原地更新，不编号、不归档）  |  最后更新: 2026-10-08
> 基线 commit: 650b8a8（M7 交付工具；本次补充实测 Qt 依赖下限）  |  适用版本: 0.0.1
> 形态: 完整  |  维护: AI 更新（见 `.agents/rules/15_architecture_doc.md`）
> 读法: 第 1、2、3、6 节为必读区（每次会话读，合计 ≤150 行）；其余按需读

## 1. 这个项目是干什么的（必读）

- **一句话**：serialforge 为 Windows x64 应用提供基于 QtCore 信号槽和线程的串口通讯、设备发现与命令调度能力。
- **背景与动因**：把串口字节流、帧定界、设备探测、命令响应和日志封装为可发布到 PyPI 的库，让 PySide6 应用只接触稳定的门面 API。
- **明确不做**：不包含任何具体设备业务命令；不使用 asyncio 或多进程；不导入 QtWidgets/QtGui；不承诺 Linux、32 位 Windows 或硬件无关的真机行为。

## 2. 外部形态（必读）

- 交付物：可构建的 Python 库 `serialforge` 与本地 CI 脚本；无硬件 Demo 放在 `examples/`，测试替身放在 `tests/support/`，不进入发布 wheel。
- 运行方式：使用方 `from serialforge import ...`；业务命令运行时声明并注册；Qt 应用提供 `QCoreApplication` 事件循环。
- 关键依赖：Python >=3.11；`PySide6-Essentials>=6.11.2`（只用 QtCore）、`pyserial>=3.5`、`loguru>=0.7`；uv + hatchling 构建。Qt 下限取本次完整套件通过的保守值，旧版存在 connect 名称冲突。

## 3. 模块地图（必读）

| 模块 / 目录 | 职责（一句话） | 入口（文件:类） | 对外提供 | 依赖谁 | 被谁依赖 |
| --- | --- | --- | --- | --- | --- |
| `src/serialforge/enums.py` | 稳定枚举 | 定义层 | 顶层与 advanced 枚举 | 标准库 | 所有层 |
| `src/serialforge/models.py` | 不可变声明、结果与配置数据类 | 定义层 | 顶层日常数据类 | enums/errors/advanced 类型约束 | 所有层 |
| `src/serialforge/errors.py` | 异常层次 | 定义层 | `errors` 命名空间 | 标准库 | 所有层 |
| `src/serialforge/settings.py` | 默认配置常量 | 定义层 | 内部默认值 | 标准库 | 定义层与实现层 |
| `src/serialforge/protocols.py` | 后端与传输协议 | 定义层 | 内部协议 | models | transport/discovery/connection |
| `src/serialforge/advanced.py` | 进阶配置和统计再导出 | 再出口 | `advanced.__all__` | 定义层 | 使用方与实现层 |
| `src/serialforge/transport/` | 字节流、定界、校验、延迟与端口后端 | `__init__.py`：M2 传输组件 | 内部模块 | L0 | discovery/connection |
| `src/serialforge/connection/` | 命令注册、响应解析、调度与连接生命周期 | `command_registry.py:CommandRegistry`、`command_dispatcher.py:CommandDispatcher`、`serial_handler.py:SerialHandler`、`connection_worker.py:ConnectionWorker` | 顶层门面与内部调度组件 | L0-L2 | `__init__.py` |
| `src/serialforge/discovery/` | 端口扫描、波特率探测、缓存与异步发现 | `device_finder.py:DeviceFinder`、`port_scanner.py:PortScanner`、`baud_prober.py:BaudProber` | `DeviceFinder` | L0-L1 | connection/顶层 |
| `src/serialforge/diagnostics/` | 流量与文件日志 | `traffic_logger.py:TrafficLogger`、`log_file_manager.py:LogFileManager` | 内部诊断组件 | L0-L1 | connection |
| `tests/support/` | 可脚本化假后端与模拟设备 | `__init__.py`：本地测试替身 | 测试内部导入 | L0-L1 | tests/、examples/ |
| `tests/` | 契约、守卫、单元和 API 快照 | `test_*.py` | 本地验证 | src | CI |
| `examples/`、`scripts/`、`docs/` | 外置 Demo、本地 CI 与发布/真机验收说明 | `demo.py`、`ci.py`、`release.py` | 开发与维护者工具，不发布测试替身 | 源码或安装的 wheel、tests/support | 使用方/本地验收 |

## 4. 交互关系（按需）

- 依赖单向：定义层 L0 → transport/diagnostics L1 → discovery L2 → connection L3 → 顶层再导出 L4；`tests/support/` 只作为测试注入边界，不是发布层。
- 典型路径：`SerialHandler.connect()` 通过 discovery 获取端口，再由 transport 读写；命令经 registry/dispatcher 匹配并以 Qt 信号发送结果。
- M1 建立定义层、命名空间与契约测试；M2 只实现可替换传输层、帧/校验/延迟基础设施和无硬件测试后端，不提前实现 connection/discovery 门面。

## 5. 状态与外部边界（按需）

- 外部 I/O：M2 起由 pyserial 访问串口；QtCore 负责线程与信号；loguru 在 M3 接入。
- 配置：人写的工具配置保留根目录 `pyproject.toml`；运行时日志、缓存和状态按项目规则进入 `data/` 或用户目录。
- 本地 CI：默认全量检查、独立 Python 3.11–3.14 矩阵、构建/twine 与外置 wheel 验收；`--fast` 单版本，`--lowest` 由 uv 在临时副本固定锁定的开发工具、解析最低运行依赖，`--smoke` 验收已构建 wheel，`--release` 覆盖全部。CI 不得改动仓库 uv.lock；正式依赖范围变更必须同步锁文件。
- 发布：构建物统一进入 `outputs/01_dist/`；scripts/release.py 默认 dry-run，先要求干净工作区并执行发布前 CI。维护者上传要求显式参数和交互确认，AI 不执行上传、push 或 tag。

## 6. 关键不变量（必读，最容易改错的地方）

- 顶层 `__all__` 恰为设计文档的 15 个名字 —— **违反**：破坏公共 API 预算和快照 —— **生效范围**：`src/serialforge/__init__.py`。
- `CommandSpec` / `EventSpec` 是 `frozen=True, eq=False` 且全链路保留同一对象 —— **违反**：`result.spec is X` 失效 —— **生效范围**：models、registry、dispatcher、Qt 信号。
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
| 设计契约 | `串口通讯模块_方案_v10定稿.md` |
| M1 进度 | `.agents/docs/checkpoint/_archive/0001_m1-foundation.md` |
| M2 交接 | `.agents/docs/checkpoint/_archive/0002_m2-transport.md` |
| 任务队列 | `.agents/docs/todo/README.md` |
| 定义层实现 | `src/serialforge/enums.py`、`models.py`、`errors.py`、`advanced.py` |

## 8. 变更日志（按需，倒序）

| 日期 | commit | 改了什么 |
| --- | --- | --- |
| 2026-10-08 | 本次提交 | 最低组合 99 项通过；Qt 下限收紧至实测 6.11.2，临时副本固定开发工具后解析最低运行依赖 |
| 2026-10-08 | 本次提交 | 补齐外置 Demo/README、独立 CI 和 release dry-run；干净 wheel 导入、Demo 及测试通过 |
| 2026-10-08 | 本次提交 | 统一绝对导入，将测试替身移至 tests/support，并禁止测试内容进入 wheel/sdist |
| 2026-10-08 | 4d253a0 | 交接核对交付边界：Demo、完整文档、Python 矩阵和干净 wheel 环境仍待 M7 |
| 2026-10-08 | e8c73ca | M5 连接工作线程、自动重连、心跳与日志会话；M6 扫描、波特率探测、缓存、取消和 COM11 真机验证完成 |
| 2026-10-08 | 526ac6f | 明确支持无结束符回复的静默定界，校验间隔并覆盖分片和动态版本号 |
| 2026-10-08 | 393c7b9 | M4 调度边界、并发写锁与流量信号背压完成；真机版本查询通过 |
| 2026-10-08 | 222044c | M4 增加注册表、响应解析、调度器及 handler 的注册发送入口 |
| 2026-10-08 | 1716749 | 同步 M3 已完成的诊断模块与阶段状态；等待确认进入 M4 |
| 2026-10-01 | 40ab7e7 | M2 传输基础设施、假后端和规范化迁移已提交 |
| 2026-09-30 | 98ba269 | 完成 M1 定义层与规范目录；进入 M2 传输层开发 |
