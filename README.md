# serialforge

> 适用版本: 0.0.1 | 公共 API 改造 R1–R5

面向 Windows 10/11 x64、Python 3.11–3.14 的串口通讯库。仅使用 QtCore，
支持 PySide6、PyQt6 与 PyQt5；导入不会创建应用、线程、串口、文件或日志 sink。
设备指令由使用方声明，库负责发现、调度、解析、重连和回调。

```powershell
uv add "serialforge[pyside6]"  # 或 serialforge[pyqt6] / serialforge[pyqt5]
```

同时安装多个绑定时，启动前设置 `QT_API=pyside6`、`QT_API=pyqt6` 或 `QT_API=pyqt5`；已加载的绑定与设置必须一致。PyQt5/PyQt6 为 Riverbank GPL v3 / 商业双授权，按其条款履行义务；Python 3.11 下限不变。

## 推荐用法：声明集中、异步闭包、扫描连接

这是 serialforge 的推荐使用形态，三步固定下来，命令表与代码骨架一一对应：

1. **声明集中一处**：建好 `SerialForge` 后立刻把协议里的命令全部 `add(...)` 存成属性，每个命令只创建一次。
2. **发送与消费写在同一个函数里**：`send_async(命令).add_done_callback(闭包)`，闭包在主线程直接改控件。
3. **扫描即配置，连接用扫描结果**：`scan_sync(baudrates=[...], probe=命令)` → `connect_sync(devices[0])`。

```python
from serialforge import Message, SerialForge
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.output = QLabel()
        self.button = QPushButton("获取波特率")
        self.connect_button = QPushButton("连接设备")

        # ① 声明集中一处：加命令只改这一段；request 不用自己写 CRLF
        self.forge = SerialForge()
        self.VERSION = self.forge.add(
            request=">SN_INFO_GET=?",
            pattern=r">SN_INFO_GET=(?!\?)(?P<sn>.+)",        # 负向预查排除回显；默认 str 返回整段匹配帧
        )
        self.BAUD_RATE = self.forge.add(
            request=">BAUD_RATE_GET=?",
            pattern=r">BAUD_RATE_GET=(?!\?)(?P<baud>\d+)",
            result_type=int,                                 # data 是 [921600]
        )

        self.button.clicked.connect(self.on_get_baud_rate)
        self.connect_button.clicked.connect(self.on_connect_device)

    # ② 发送 + 闭包在同一个函数里，闭包捕获控件
    def on_get_baud_rate(self):
        def on_done(message: Message[int]) -> None:
            self.output.setText(str(message.data))

        self.forge.send_async(self.BAUD_RATE).add_done_callback(on_done)

    # ③ 波特率与探针一次给全，用扫描返回值连接
    def on_connect_device(self):
        devices = self.forge.scan_sync(baudrates=[921600], probe=self.VERSION)
        if not devices:
            return
        self.forge.connect_sync(devices[0])
```

上面是界面骨架（省去布局与控件细节），替换成你的协议文本即可。要点：`send_async` 的目标就是你声明
过的那个对象，回调闭包统一排队到 Qt 应用主线程，所以能直接改界面，不必自己拆线程或加定时器；
默认期限 3 秒，从提交开始算，包含排队、设备等待与主线程投递，
**超时或失败不调用成功回调**，需要失败分支就用 `call.result()`（重抛 `serialforge.errors` 的异常）
或自行兜底。`scan_sync()` 与 `connect_sync()` 都阻塞调用线程，界面应用可用异步版
`scan_async()`/`connect_async()` 的闭包，或把它们放到自己的工作线程，门面不提供
扫描进度或完成信号，结果只用返回值。

完整可运行版本与更多形态见[示例](#示例)。

## 声明与发送

`add()` 返回传入/新建的那个声明对象：`add(spec)` 原对象返回，`add(request, pattern, result_type=...)`
只构造一次。声明冻结且身份保持，回调里可用 `message.spec is self.BAUD_RATE` 分发。

响应命令 request/pattern 必填，默认 `mode=SINGLE`，默认 `result_type=str` 返回整段匹配帧；
`int`/`float` 取第一个捕获组转标量，`dict` 返回命名组字典，`dataclass` 按命名字段构造。
事件写成 `add(pattern=r"ALARM:.+")`；只发送不等回复必须显式 `mode=ResponseMode.NO_REPLY`；
多行响应显式 `mode=ResponseMode.MULTI`，并在 `count>0` 与 `until` 中选一个。
可选值缺省为 `count=-1`、`until/error_pattern/parser=""`、`total_timeout_s=-1.0`，不接受 `None`。

`send_sync(target, wait_timeout_s=3.0, params=..., context=...) -> Message` 与
`send_async(target, timeout_s=3.0, params=..., context=...) -> CommandCall` 同级：target 可以是已注册
声明或完整命令文本，文本不接受额外 params。`params` 填充请求占位符；`context` 是每次调用独立的只读
浅快照，随结果和调用句柄保留，不发送到设备也不参与解析，闭包里可用它回填行号等界面状态。

命令自身的协议预算（`Spec.timeout_s` 默认 1 秒、`total_timeout_s` 默认关闭）与调用期限相互独立，
多行或慢响应要让调用期限大于协议预算，例如声明 `total_timeout_s=5.0` 时用
`send_async(spec, timeout_s=6.0)`。同步失败直接抛出 `serialforge.errors` 的执行异常；异步失败通过
`call.result()` 重抛，未完成时抛 `CommandNotReadyError`。STREAM 只能异步发送，帧从 `received` 取得，
直到 `cancel()` 或调用期限结束；界面关闭时对未完成调用 `cancel()`。未归属命令的后台错误由
`check_errors()` 抛出。

回调参数的类型注解按该命令**声明的 `result_type`** 写：默认 `str` 是 `Message[str]`，`result_type=int`
是 `Message[int]`，`dict`/dataclass 各按声明类型，一个回调服务多条命令或处理 `received` 时用
`Message[Any]`。`Message` 是泛型，直接下标即可；`Message.data` 始终是 `list[T]`：单条响应是单元素列表，
多条响应保留完整列表，无回复是空列表，所以回调体不需要额外的 `isinstance` 判断。

## 扫描与连接

`scan_sync(baudrates=[...], probe=spec)` 自带完整配置，不必先 `configure()`；也接受 `configure` 的字段形式、
字符串探针（须补 `probe_pattern`）或现成的 `DeviceProfile`。签名重载：

```python
devices = forge.scan_sync(baudrates=[115200, 921600], probe=PROBE)  # 阻塞，返回 list[DeviceInfo]
devices = forge.scan_sync(port="COM11", baudrates=[921600], probe=PROBE)
forge.cancel_scan()                                                # 线程安全请求取消
```

`probe` 用已声明对象时必须是 `SINGLE`、无占位符，且 pattern 不能匹配自己的请求回显（否则 `ConfigError`），
上面示例的 `(?!\?)` 就是为此。无匹配返回 `[]`，配置仍保留；扫描不自动连接，结束保持断开。
界面应用也可以不阻塞：`scan_async()` 返回 `ScanCall`，`connect_async()` 返回 `ConnectCall`，
`add_done_callback` 在主线程拿到设备列表/已连接设备；失败不回调，用 `call.result()` 重抛异常；
`call.cancel()` 中止并抑制回调。
个别端口打不开、写不进或没有应答只跳过该端口，不会让整次扫描失败，也不抛库异常。
探测按端口并行、同端口速率串行，端口只打开一次；打开调用默认限时 1 秒，卡死的驱动直接跳过
（`RuntimeConfig.probe_open_timeout_s`），每个速率还会先用 0.3 秒活性窗口筛“有没有回包字节”
（`probe_activity_window_s`，`0` 关闭；只对任意波特率都回包的设备有加速效果）。
`ScanMode.ALL` 扫完全部，`ScanMode.FIRST_MATCH` 拿到第一个成功者就取消其余探测。
`connect_sync()` 有三种入口：

```python
forge.connect_sync()                 # 按保存策略发现首个设备
forge.connect_sync("COM11")          # 指定端口，绕过 USB 身份过滤但仍探测波特率
forge.connect_sync(devices[0])       # 用扫描确认过的 DeviceInfo 直接打开，跳过重新探测
```

`connect_sync()` 阻塞到设备打开并完成初始化，返回即可以发送；每次 `connect_sync`/`scan_sync` 都会先完整断开旧会话。
VID/PID 可省略，只在提供时用于过滤。`configure(...)` 只在空闲断开时原子替换策略，
`heartbeat_s=-1.0` 禁用心跳，`port=""` 不限制端口。无结束符设备用接收定界，与发送终止符相互独立：

```python
from serialforge.advanced import FramingConfig, FramingMode

forge.configure(baudrates=[115200], probe=PROBE,
                framing=FramingConfig(mode=FramingMode.SILENCE_GAP, silence_gap_s=0.08))
```

## 收发观察与日志

门面仅公开三个信号：`connection_state_changed(ConnectionState)`、`received(Message)` 和
`raw_sent(bytes)`，都在 Qt 应用主线程投递。`received` 的 `category` 区分 `RAW_RECEIVE`、
`RESPONSE_FRAME`、`COMMAND_RESULT`、`DEVICE_EVENT`、`STREAM_FRAME`、`UNKNOWN_FRAME`；
结果信号与同步返回/异步 `result()`/成功回调共享同一个最终 `Message` 对象。

滚动日志只选 `RAW_RECEIVE` 的 `raw_data` 与 `raw_sent` 的字节：这是完整实际读写字节，包含结束符、
回显、探测、初始化、心跳和重试。`raw_sent` 只含底层确认接收的字节，部分写入只记录已接收前缀；
解析帧和终态来自同一物理回复，混入日志会重复。并发扫描的裸 TX 字节没有端口字段。
`traffic_logged` 不再作为公开信号；库内 loguru DEBUG/文件日志保持默认静默。

## 示例与兼容迁移

`examples/` 用共享的虚拟设备演示无硬件流程，四种形态各有脚本：

```powershell
uv run python examples/quick_start.py       # 单发单收 / 多收 / 流式 / 主动上报 / 无回复
uv run python examples/auto_connect.py      # scan 模式与三种 connect_sync 入口
uv run python examples/async_callbacks.py   # 推荐的 send_async + add_done_callback 闭包
uv run python examples/interactive.py       # 交互式控制台
```

测试替身只在源码仓库的 `tests/support/`，不进入客户 wheel/sdist。

过渡期顶层保留原 15 个名字，再加 `SerialForge`/`Spec`/`Message`/`MessageCategory`，共 19 个。
日常使用统一 API；进阶配置在 `serialforge.advanced`，异常在 `serialforge.errors`。
`SerialHandler` 保留 register/unregister/send/send_and_wait 方法桥接，但不再提供旧结果、事件、错误和
流量信号。旧 `CommandSpec`/`EventSpec` 构造及身份语义仍可用；新返回值统一为 `Message`，
不保证旧结果类型的 `isinstance`。下一轮移除兼容入口的版本由用户决定。

## Windows 范围

支持 Windows 10/11 x64 和 64 位 Python。睡眠唤醒、USB 选择性挂起、驱动 DTR/RTS 脉冲、拔插重连和
TX-RX 回环必须按
[.agents/docs/knowledge/software/0003_Windows真机验证矩阵.md](.agents/docs/knowledge/software/0003_Windows真机验证矩阵.md)
中的矩阵由使用方验证；当前仓库没有宣称整张矩阵通过。TX-RX 回环要把独立适配器的 TX 与 RX 物理短接
后再测，接到目标设备时它不会回显 `SERIALFORGE_LOOPBACK`。PyInstaller 使用方还需自行履行 PySide6
LGPL 合规义务。遇到挂起导致的掉线，可以检查 Windows 的 USB 选择性暂停和设备电源管理选项；
库不会修改系统设置。日志和波特率缓存使用 Qt 应用数据目录，PyInstaller 打包后也不应改成程序目录。

## 开发与验收

```powershell
uv sync
uv run python scripts/ci.py --fast
uv run python scripts/ci.py
```

完整本地 CI 包含 Python 3.11–3.14 独立环境、格式、ty、pytest、pylint、构建、twine 和干净 wheel 冒烟。
`--lowest` 单独验证最低直接依赖；`--release` 包含全部检查。
`--smoke --wheel <路径> --python 3.11` 可在另一台机器对已构建 wheel 验收。

文档站点使用 mkdocs-material（开发依赖），页面源码在 `docs/`，配置见 `mkdocs.yml`：

```powershell
uv run mkdocs serve              # 本地预览 http://127.0.0.1:8000
uv run mkdocs build --strict     # 构建到 outputs/04_docs_site/
```

发布流程见
[.agents/docs/knowledge/software/0002_发布流程.md](.agents/docs/knowledge/software/0002_发布流程.md)。
本项目不生成云端 CI，不执行 PyPI 上传、推送或打 tag。

推荐用法与迁移口径见
[.agents/docs/knowledge/software/0011_推荐用法_集中声明与异步闭包.md](.agents/docs/knowledge/software/0011_推荐用法_集中声明与异步闭包.md)；
已落地的公共 API 契约索引见
[.agents/docs/knowledge/software/0005_公共API改造_契约索引与迁移.md](.agents/docs/knowledge/software/0005_公共API改造_契约索引与迁移.md)，
AI 协作入口见 `AGENTS.md`。
