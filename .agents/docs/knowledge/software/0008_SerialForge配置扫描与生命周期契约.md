# 0008_SerialForge配置扫描与生命周期契约

> 分类: knowledge/software | 创建: 2026-10-09 | 适用范围: 门面配置、扫描、连接
> 状态: R2–R4 已接入源码；R5 双绑定与完整矩阵验收进行中

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
- profile=None 时可先 add/configure，scan/connect 前必须有完整策略。不会默认向全部端口发送探测指令。
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

### 6.3 scan：直接接收配置，返回设备列表

```python
@overload
def scan(self, *, mode: ScanMode = ScanMode.ALL,
         port: str = "") -> list[DeviceInfo]: ...

@overload
def scan(self, profile: DeviceProfile, /, *, mode: ScanMode = ScanMode.ALL,
         port: str = "") -> list[DeviceInfo]: ...

@overload
def scan(self, *, baudrates: Sequence[int], probe: ProbeInput,
              vid_pid: Sequence[tuple[int, int]] = (),
              name: str = "", terminator: str = "\r\n",
              encoding: str = "utf-8", echo: bool = False,
              heartbeat_s: float = -1.0, serial: SerialConfig | None = None,
              framing: FramingConfig | None = None,
              runtime: RuntimeConfig | None = None,
              mode: ScanMode = ScanMode.ALL,
              port: str = "") -> list[DeviceInfo]: ...

@overload
def scan(self, *, baudrates: Sequence[int], probe: str,
              probe_pattern: str, probe_timeout_s: float = 1.0,
              vid_pid: Sequence[tuple[int, int]] = (),
              name: str = "", terminator: str = "\r\n",
              encoding: str = "utf-8", echo: bool = False,
              heartbeat_s: float = -1.0, serial: SerialConfig | None = None,
              framing: FramingConfig | None = None,
              runtime: RuntimeConfig | None = None,
              mode: ScanMode = ScanMode.ALL,
              port: str = "") -> list[DeviceInfo]:
    """保存完整配置并阻塞扫描；返回前回收所有临时探测句柄。"""

def cancel_scan(self) -> None:
    """线程安全地请求取消扫描；当前无扫描时无操作。"""
```

无配置参数使用保存策略；提供 Profile 或字段的形式先 configure 再扫描，后续 connect 直接复用。无匹配返回 []，配置仍保留；扫描不自动连接。重复/并发扫描抛 ProbeError，基础设施故障直接抛 ProbeError，不全部吞成“无设备”。取消返回已完成的匹配，只通过函数返回值交付，不发完成信号。

ALL 查全部，FIRST_MATCH 协作取消并回收后至多返回一个。不同端口并行，同端口波特率串行。指定 port 绕过 USB 过滤但仍探测。连接活跃时拒绝 scan，避免扫描自己占用的端口。

### 6.4 连接与关闭

```python
def connect(self, target: str | DeviceInfo = "") -> None:
    """阻塞发现、打开和初始化；成功返回表示可以发送。"""

def disconnect(self) -> None:
    """停止并回收 I/O 线程；未完成调用以 CANCELLED 完成；重复调用无操作。"""

@property
def state(self) -> ConnectionState:
    """当前连接状态，只读。"""

def set_init_sequence(self, items: Sequence[Spec[Any] | str]) -> None:
    """设置连接/重连后顺序执行的初始化命令；须已注册且参数完整。"""
```

target="" 按保存策略发现首个设备；字符串端口绕过身份过滤但仍探测；有 baudrate 的 DeviceInfo 直接打开。失败抛 SerialForgeError，connect 返回值继续 None。另一线程可通过 disconnect 取消未完成连接。

connect/disconnect 与 QObject 方法名有冲突，暂保留熟悉命名，但必须覆盖此前旧 PySide6 的 connect 名称冲突问题；不能只换 import 就宣布多绑定兼容。

## 新旧配置桥接

门面值哨兵只在构建旧 DeviceProfile 的边界转换：heartbeat_s=-1 转成旧 None，target/port="" 转成旧 None；已有配置对象保留身份。配置对象本身仍可为 None。不要将旧定义层提前改为尚未交付的最终门面模型。
