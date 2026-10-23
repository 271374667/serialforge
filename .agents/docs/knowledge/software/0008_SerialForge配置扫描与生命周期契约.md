# 0008_SerialForge配置扫描与生命周期契约

> 分类: knowledge/software | 创建: 2026-10-09 | 适用范围: 门面配置、扫描、连接
> 状态: R1–R5 已实现并通过软件验收；真机矩阵未完成

## 6. 最终门面 SerialForge

### 6.1 构造与应用生命周期

```python
class SerialForge(QObject):
    """管理一个设备连接的配置、扫描、声明、命令调度和信号通知。"""

    def __init__(self, profile: DeviceProfile | None = None, *,
                 log: LogConfig | None = None, allow_raw_text: bool = True,
                 application: Literal["existing", "core"] = "existing") -> None:
        """创建断开状态的门面；不打开串口、不启动扫描。"""
```

- existing：要求已有 QCoreApplication 或其子类实例，否则 SerialForgeError；适合 Qt 界面应用。
- core：已有实例则复用；没有时显式创建并强引用 QCoreApplication，不自动执行 exec()。适合纯同步脚本，创建发生在构造时，导入仍无初始化副作用。
- core 不适合稍后才创建 QApplication 的程序；GUI 使用者先创建自己的应用再构造门面。单进程只有一种 Qt 绑定、一个应用实例。
- profile=None 时可先 add/configure，scan_sync/scan_async/connect_sync/connect_async 前必须有完整策略。不会默认向全部端口发送探测指令。
- 多门面可管理不同端口；core 应用引用由共享内部持有者管理，关闭一门面不销毁其他门面使用的应用。

“只使用一个类”指不强迫手工构建库的 Profile/Spec/Finder。GUI 应用仍负责自身 Qt 应用；纯同步脚本可用 core 省去显式创建 Qt 类。

### 6.2 设备配置

```python
@overload
def configure(self, profile: DeviceProfile, /) -> None: ...

@overload
def configure(self, *, baudrates: Sequence[int], probe: ProbeInput,
              vid_pid: Sequence[tuple[int, int]] = (),
              name: str = "", terminator: str = "\r\n",
              encoding: str = "utf-8", echo: bool = False,
              heartbeat_s: float = -1.0, serial: SerialConfig | None = None,
              framing: FramingConfig | None = None,
              runtime: RuntimeConfig | None = None) -> None: ...

@overload
def configure(self, *, baudrates: Sequence[int], probe: str,
              probe_pattern: str, probe_timeout_s: float = 1.0,
              vid_pid: Sequence[tuple[int, int]] = (),
              name: str = "", terminator: str = "\r\n",
              encoding: str = "utf-8", echo: bool = False,
              heartbeat_s: float = -1.0, serial: SerialConfig | None = None,
              framing: FramingConfig | None = None,
              runtime: RuntimeConfig | None = None) -> None:
    """验证并保存完整设备策略；字符串探测必须填写 probe_pattern。"""
```

vid_pid=() 表示不按 USB 身份过滤；baudrates 非空、唯一、正整数。heartbeat_s=-1.0 表示禁用，启用时必须有限正数；port="" 表示不指定端口；这属于值字段哨兵，不再传 None。terminator 只控制发送；framing 单独控制接收。串口默认 8N1 等策略沿用现有配置；复杂参数仍可选用 advanced 配置对象。

probe 为字符串的重载中，probe_pattern: str 必填且无默认值；内部只构造一次 SINGLE Spec，使用 probe_timeout_s。probe 为 Spec/ProbeSpec 的重载根本不提供 probe_pattern/probe_timeout_s 参数，直接保留原对象，不复制。Spec 探测必须无占位符、SINGLE，且响应不能匹配请求回显。

这是完整替换，不作隐式局部合并。对象与字段形式混用、无效配置抛 ConfigError；先验证后原子替换，失败保持旧策略。扫描、连接、重连或未完成调用期间禁止替换。新 terminator 与已有注册表一并重新校验，冲突拒绝替换，但不重建已有 Spec。

### 6.3 scan_sync / scan_async：直接接收配置，返回设备列表

```python
@overload
def scan_sync(self, *, mode: ScanMode = ScanMode.ALL,
         port: str = "") -> list[DeviceInfo]: ...

@overload
def scan_sync(self, profile: DeviceProfile, /, *, mode: ScanMode = ScanMode.ALL,
         port: str = "") -> list[DeviceInfo]: ...

@overload
def scan_sync(self, *, baudrates: Sequence[int], probe: ProbeInput,
              vid_pid: Sequence[tuple[int, int]] = (),
              name: str = "", terminator: str = "\r\n",
              encoding: str = "utf-8", echo: bool = False,
              heartbeat_s: float = -1.0, serial: SerialConfig | None = None,
              framing: FramingConfig | None = None,
              runtime: RuntimeConfig | None = None,
              mode: ScanMode = ScanMode.ALL,
              port: str = "") -> list[DeviceInfo]: ...

@overload
def scan_sync(self, *, baudrates: Sequence[int], probe: str,
              probe_pattern: str, probe_timeout_s: float = 1.0,
              vid_pid: Sequence[tuple[int, int]] = (),
              name: str = "", terminator: str = "\r\n",
              encoding: str = "utf-8", echo: bool = False,
              heartbeat_s: float = -1.0, serial: SerialConfig | None = None,
              framing: FramingConfig | None = None,
              runtime: RuntimeConfig | None = None,
              mode: ScanMode = ScanMode.ALL,
              port: str = "") -> list[DeviceInfo]:
    """先断开当前设备、保存可选配置并阻塞扫描；返回前回收临时探测句柄。"""

def cancel_scan(self) -> None:
    """线程安全地请求取消扫描；当前无扫描时无操作。"""
```

用户 2026-10-10 明确覆盖原扫描限制：每次 scan_sync/scan_async 先完整 disconnect 当前设备，取消旧调用/回调、回收串口线程和句柄、清空接收分片，再应用可选配置并开始探测。无配置参数使用保存策略；提供 Profile 或字段的形式在断开后 configure 再扫描，后续 connect 直接复用。旧连接回收失败时直接抛错，不替换配置或开始探测。

无匹配返回 []，配置仍保留；扫描不自动连接，也不恢复旧连接，成功、无匹配、取消或探测失败后都保持断开。探测是查询接口（2026-10-19 用户覆盖）：个别端口打不开、写不进、定界失败或应答超时只跳过该候选并继续扫其余端口，最终无命中返回 []；库不把这类环境故障包装成 ProbeError 抛给调用方（含 connect 内部发现）。准备阶段和探测阶段仍拒绝同门面的并发 scan_sync/scan_async/connect/configure：门面重复/并发扫描或已有 connect 未结束时抛 ConfigError，同一 finder 重复 find 抛 ProbeError，配置非法抛 ConfigError——只有配置错误与误用才报错。取消返回已完成的匹配，只通过函数返回值交付，不发完成信号。

ALL 查全部，FIRST_MATCH 协作取消并回收后至多返回一个。不同端口并行，同端口波特率串行。指定 port 绕过 USB 过滤但仍探测。旧连接释放串口所有权后才枚举端口，因此原连接设备也能再次被扫描发现。独立 configure 仍要求空闲断开，不隐式触发 disconnect。

探测耗时控制（2026-10-19 用户要求）：每个端口只 open 一次，其余候选速率在同一句柄上切换（`TransportProtocol.set_baudrate`）；open 在可放弃的辅助线程中执行，受 `RuntimeConfig.probe_open_timeout_s`（默认 1 秒）约束，超时即跳过该端口，辅助线程稍后拿到句柄会立即关闭并释放端口租约，避免驱动卡死把整次扫描拖到系统级超时。每个速率先用 `RuntimeConfig.probe_activity_window_s`（默认 0.3 秒，0 关闭）观察是否收到非回显字节：没有字节就提前换下一个速率，有字节才继续等满探针自身的 `timeout_s`。该筛选只对“任意波特率都会回包”的协议有加速作用，不能替代正则确认；纯回显不计入活性，正确速率下响应慢于窗口的设备会被误筛，慢启动设备应调大窗口或设 0 关闭。

异步扫描（2026-10-19 用户要求）：`scan_async(...)` 与 `scan_sync` 同参，立即返回 `advanced.ScanCall`；后台线程执行同一条扫描路径（先 disconnect、可选 configure、再探测），成功闭包经 Qt 主线程桥接收到 `list[DeviceInfo]`，用法与 `send_async` 的 `add_done_callback` 一致。失败不执行成功闭包，错误由 `result()` 重抛；`cancel()` / `disconnect()` 请求取消并抑制回调，取消完成后 `result()` 返回已完成匹配。异步扫描不设额外 3 秒投递期限，期限由 `probe_total_timeout_s` / `probe_open_timeout_s` 决定；异步期间门面同步拒绝并发 scan_sync/scan_async/connect_sync/connect_async/configure。

### 6.4 连接与关闭

```python
def connect_sync(self, target: DeviceInfo | list[DeviceInfo] | None = None) -> None:
    """先关闭旧连接，再阻塞发现、打开和初始化；成功返回表示可以发送。"""

def connect_async(self, target: DeviceInfo | list[DeviceInfo] | None = None) -> ConnectCall:
    """后台执行同一条连接路径；闭包在主线程收到已连接的 DeviceInfo。"""

def list_devices(self) -> list[DeviceInfo]:
    """枚举全部可用端口为未探测 DeviceInfo；不开口、不探测、不过滤。"""

def disconnect(self) -> None:
    """停止并回收 I/O 线程；未完成调用以 CANCELLED 完成；重复调用无操作。"""

@property
def state(self) -> ConnectionState:
    """当前连接状态，只读。"""

def set_init_sequence(self, items: Sequence[Spec[Any] | str]) -> None:
    """设置连接/重连后顺序执行的初始化命令；须已注册且参数完整。"""
```

target 语义（2026-10-20 用户覆盖）：只接受 `DeviceInfo | list[DeviceInfo] | None`；None 或空列表按保存策略自动发现；非空列表只取第一个；DeviceInfo 直接打开，无 baudrate 时用配置首档波特率。端口名字符串抛 ConfigError——端口名会随插拔变化，调用方应先用 `list_devices()` 枚举全部可用端口（不过滤 VID/PID、不探测、可先于 configure 调用、不影响扫描/连接互斥、排除已连接持有的端口）让用户选择，或用 scan_sync/scan_async 得到含波特率的 DeviceInfo。失败抛 SerialForgeError，connect_sync 返回值继续 None。另一线程可通过 disconnect 取消未完成连接。

用户 2026-10-10 明确：同一个 SerialForge 实例最多管理一个设备连接。每次 connect_sync/connect_async 都先执行完整 disconnect，关闭旧串口并等待旧工作线程退出、终止旧日志会话、取消未完成命令和待投递成功回调、清空接收分片，再发起新连接。目标与旧设备相同也必须关闭重新连接，照常重新探测（端口名/自动发现）、打开和执行初始化序列；不作同设备短路。已有声明、配置和初始化序列保留。

旧连接回收失败时直接抛错，不启动新连接；新目标连接失败时保持失败状态，不恢复旧设备，可再次 connect_sync 重试。扫描或另一连接尚未结束时仍拒绝并发连接；自动重连中的旧会话可以由新的连接停止并替换。

新门面用 connect_sync/connect_async 命名，避开 QObject.connect 的重名冲突；兼容 SerialHandler.connect 仍保留旧名，`disconnect` 继续按业务语义覆盖 QObject 方法。多绑定兼容不能只换 import 就宣布通过。

异步连接（2026-10-19 用户要求）：`connect_async(...)` 立即返回 `advanced.ConnectCall`；后台线程执行与 `connect_sync` 相同的路径（先 disconnect、再发现/打开/初始化），成功闭包经 Qt 主线程桥接收到已连接的 `DeviceInfo`。失败不执行成功闭包，错误由 `result()` 重抛；`cancel()` / `disconnect()` 中止进行中的连接并抑制回调。连接不设额外 3 秒投递期限，由打开、settle 与初始化预算决定；异步期间门面同步拒绝并发 scan_sync/scan_async/connect_sync/connect_async/configure。

## 新旧配置桥接

门面值哨兵只在构建旧 DeviceProfile 的边界转换：heartbeat_s=-1 转成旧 None，target/port="" 转成旧 None；已有配置对象保留身份。配置对象本身仍可为 None。不要将旧定义层提前改为尚未交付的最终门面模型。
