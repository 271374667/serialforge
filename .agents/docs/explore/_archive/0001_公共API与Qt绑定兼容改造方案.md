> 归档时间: 2026-10-09  |  归档原因: 用户已授权实施；目标契约已保存为knowledge/software/0005及细则，任务0008负责R1至R5  |  原路径: .agents/docs/explore/0001_公共API与Qt绑定兼容改造方案.md

# 0001_公共 API 与 Qt 绑定兼容改造方案

> 分类: explore | 日期: 2026-10-09 | 结论: 技术上可行，待用户评审
> 状态: 仅方案；未修改库源码、测试、依赖或现行架构契约
> 基线: 当前 dev 工作树；现有 M7 Windows/芯片真机矩阵仍待完成

## 1. 推荐方案与评审重点

目标是让普通使用者只需要一个门面对象，就能配置设备、扫描、连接、声明命令和事件，并自由选择同步或异步发送。

| 需求 | 推荐设计 | 取舍 |
| --- | --- | --- |
| 正式同步入口 | send_sync() / send_async(timeout_s=3.0) | 同步返回或抛出异常；异步返回 CommandCall |
| 合并声明 | 一个不可变 Spec，以是否提供 request 推断命令/事件 | 合并用户概念，内部仍保留两种协议语义 |
| 合并结果 | Message.category 区分命令结果、事件、流帧和接收原字节 | 门面统一 received(Message)，发送 raw_sent(bytes) |
| 单类门面 | class SerialForge(QObject) | 类名已按用户本轮意见确定 |
| 不手工创建配置 | configure/scan/add 接受字段，内部构建对象 | 同时保留对象重载，方便进阶使用和复用 |
| 多 Qt 绑定 | 首期评估 QtPy + PySide6/PyQt6 | Python 版本和 Qt5 支持单独验收 |

本轮确定：Spec 尽量使用同类型的缺省值；异步返回对象提供 add_done_callback(callback)，自动在应用主线程执行；send_async 默认总期限 3 秒，超时不执行闭包；删除 scan_finished/error_occurred/traffic_logged，合并原命令/事件信号，增加原始发送信号。

下文全部为目标 API 伪代码，尚未实现。用户明确要求持续修订文档，只有明确说“开始修改代码”后才能实施；认可某个设计点不等于授权开发。

## 2. 当前实现与修改边界

已核对 models.py、advanced.py、顶层导出，以及 connection 下的 serial_handler、pending_command、command_dispatcher 和 discovery/device_finder。背景设计为根目录 `串口通讯模块_方案_v10定稿.md`，最新阻塞连接契约见 `.agents/docs/knowledge/software/0004_阻塞连接与可选USB身份契约.md`。

- SerialHandler.send() 已是正式异步接口，返回 advanced.CommandTicket。
- send_and_wait(timeout=...) 已返回 CommandResult，但拒绝在运行 Qt 事件循环的线程使用，文档定位为脚本线程。它临时订阅整个完成信号再按 request_id 过滤，建议改为每次调用独立完成状态。
- connect() 与 DeviceFinder.find() 已阻塞且不依赖调用线程事件泵，继续沿用。
- EventSpec 是主动上报声明；DeviceEvent 还承载 STREAM 和未知帧。主动上报没有命令成功/失败语义。
- 当前构造门面/扫描器要求外部已创建 QCoreApplication；源码直接导入 PySide6.QtCore。
- 顶层固定导出 15 个名字、声明对象不能复制、仅 QtCore、默认静默均是现行规则。

实现阶段需要修订固定导出名单、旧声明/结果契约与 Qt 依赖范围，并同步 AGENTS.md、设计补充、架构总览、API 快照和使用文档。现在不修改这些现行文件，避免把计划写成已落地事实。

维持 Windows 10/11 x64、首期 Python >=3.11、uv/hatchling/src 布局、仅 QtCore、绝对导入、默认静默、导入无资源初始化副作用、测试替身仅 tests/support。不得主动合并 master、push 或上传 PyPI。

## 3. 类型与缺省值约定

T 是解析结果类型；以下为 Python 3.11 伪代码，不是实现。

```python
class SpecRole(Enum):
    COMMAND = "command"
    EVENT = "event"

class MessageCategory(Enum):
    COMMAND_RESULT = "command_result"
    RESPONSE_FRAME = "response_frame"
    DEVICE_EVENT = "device_event"
    STREAM_FRAME = "stream_frame"
    UNKNOWN_FRAME = "unknown_frame"
    RAW_RECEIVE = "raw_receive"

# 在现有枚举增加真实“不适用”项：
# ResponseMode.NONE：事件不具有命令响应模式。
# CommandStatus.NONE：原始接收/事件没有命令终态。
# SendRoute.NONE：设备主动上报/原始接收没有发送路径。
ProbeInput = Spec[Any] | ProbeSpec
```

值字段保持原本值类型：count=-1、未设置的超时=-1.0、字符串=""、枚举使用默认值或明确 NONE 成员。对象配置缺省可以使用 None。不能用虚假的 OK 状态表示无状态，也不能用 SINGLE 表示事件模式。

parser 的正常值是可调用对象，"" 是缺省哨兵，必须如实标注 Callable[[str], T] | Literal[""]；不能标成单一 Callable 类型却给字符串默认值。result_type 是类型对象，默认 str，保持 type 类型；不使用 "" 代替类型对象，否则每次解析都需猜测它究竟是类型还是字符串。

## 4. 统一声明 Spec：减少 None，必填项不放宽

### 4.1 构造重载

同一个 frozen=True、eq=False 的 Spec 使用以下三个自定义构造器重载。响应命令 request/pattern 必填；事件 pattern 必填；NO_REPLY 必须显式选择。

```python
@overload
def __init__(self, request: str, pattern: str,
             result_type: type[T] = str, *, name: str = "",
             mode: Literal[ResponseMode.SINGLE, ResponseMode.MULTI,
                           ResponseMode.STREAM] = ResponseMode.SINGLE,
             correlation: Correlation = Correlation.EXCLUSIVE,
             count: int = -1, until: str = "", error_pattern: str = "",
             timeout_s: float | Literal[TimeoutPolicy.AUTO] = 1.0,
             idle_timeout_s: float = 0.3, total_timeout_s: float = -1.0,
             idempotent: bool = False,
             parser: Callable[[str], T] | Literal[""] = "") -> None:
    """定义响应命令；默认单帧、返回匹配帧原文；parser 为空时使用内置解析。"""

@overload
def __init__(self, request: str, *,
             mode: Literal[ResponseMode.NO_REPLY],
             name: str = "", idempotent: bool = False) -> None:
    """定义只发送的命令；不接收 pattern、解析器和响应等待参数。"""

@overload
def __init__(self, *, pattern: str, result_type: type[T] = str,
             name: str = "",
             parser: Callable[[str], T] | Literal[""] = "") -> None:
    """定义设备主动事件；pattern 必填，不接收命令专属参数。"""
```

签名是契约展示：实现静态重载需将省略 result_type 的返回类型明确为 Spec[str]，显式传 type[T] 的形式为 Spec[T]，不能让未绑定的 T 或 type[T]=str 通过伪代码掩盖类型检查问题。add 也遵守同一规则。

### 4.2 各字段的真实默认行为

| 参数/属性 | 类型与缺省 | 明确语义 |
| --- | --- | --- |
| 响应 request/pattern，事件 pattern | 必填 str，无默认 | 缺少或空字符串报错 |
| 响应 mode | ResponseMode.SINGLE | 真实默认；count 不自动改变模式 |
| name | str="" | 内部推导非空显示名 |
| count | int=-1 | 不使用计数终止；合法值为 -1 或正整数，禁止 0/其他负数 |
| until/error_pattern | str="" | 不使用对应规则；非空才编译正则 |
| total_timeout_s | float=-1.0 | 无额外协议总时限；合法值为 -1 或有限正数 |
| parser | Callable[[str], T] 或 "" | "" 使用内置解析；其他字符串和 None 都报错 |
| result_type | type，默认 str | 默认整段匹配帧文本；显式 dict 提取命名组；int/float 转标量；数据类按字段映射 |
| role | SpecRole | 按重载推导，不需用户传入 |
| 事件 request/mode | "" / ResponseMode.NONE | 真正没有发送文本和命令模式 |
| NO_REPLY pattern | "" | 构造器不提供 pattern 参数，不表示响应命令可以省略 pattern |
| 非适用的 count/until/错误规则 | -1 / "" | 保持字段类型，无需 None |
| priority | CommandPriority.NORMAL | 发送默认优先级 |

result_type 默认 str 是本轮新增选择，改变了旧默认命名组字典语义。需要字典时明确 result_type=dict；这项改变纳入迁移说明。parser 非空时优先调用 parser，返回值必须符合声明的 result_type；不匹配抛解析异常。原协议安全校验仍保留。

MULTI 必须显式 mode=MULTI，count>0 和 until!="" 二选一；其他模式必须 count=-1、until=""。STREAM 也必须显式选择且 pattern 必填。Spec("Read") 无效；Spec("Reboot", mode=NO_REPLY) 才是不等回复。

Spec 的持久字段：role: SpecRole；request/pattern/name/until/error_pattern: str；mode: ResponseMode；count: int；result_type: type；parser: Callable | Literal[""]；total_timeout_s: float。事件、NO_REPLY 等模式使用上述明确哨兵，不恢复为 None。

### 4.3 合并边界与身份

合并仍推荐一个 Spec。原对象被 add、调度、解析与结果持有，不复制、不重建。相同格式既用于回复又用于上报时须靠协议标签区分，不能由 context 或类名猜测归属。

## 5. 统一消息 Message

```python
@dataclass(frozen=True)
class Message(Generic[T]):
    """携带协议数据、调用结果或原始接收字节；category 标识消息用途。"""
    category: MessageCategory
    timestamp: float                         # monotonic 秒
    data: T | list[T] | None = None           # 业务对象；可能确实没有数据
    spec: Spec[T] | None = None               # 声明对象；未知帧/RAW 可以没有
    request_id: str = ""                      # 没有调用关联
    raw_frames: tuple[bytes, ...] = ()        # 定界后的帧，不含结束标记
    raw_data: bytes = b""                     # RAW_RECEIVE 的实际 read 字节
    context: Mapping[str, object] = field(default_factory=dict)
    status: CommandStatus = CommandStatus.NONE
    route: SendRoute = SendRoute.NONE
    params: Mapping[str, object] = field(default_factory=dict)
    sent: bytes = b""
    elapsed_s: float = -1.0                   # 无调用耗时
    error_message: str = ""

    @property
    def ok(self) -> bool:
        """仅 COMMAND_RESULT 且 status=OK 时 True。"""

    @property
    def raw_frame(self) -> bytes:
        """恰一帧返回该帧，否则 b''；调用者用 raw_frames 判断真实帧数。"""
```

| category | spec/request_id/context | 有效载荷 | 用途 |
| --- | --- | --- | --- |
| RAW_RECEIVE | None / "" / 空映射 | raw_data 为本次真实 read 字节，含设备发送的结束标记 | 滚动 RX 原始日志，处理前发出 |
| RESPONSE_FRAME | 对应命令/ID/context | 单个匹配帧和解析值 | MULTI 中间帧等按帧观察 |
| COMMAND_RESULT | 原命令或 RAW 的 None / 调用 ID / context | SINGLE 单值、MULTI 列表或无回复 None | 同步返回、异步回调、统一终态观察 |
| DEVICE_EVENT | 原事件 / "" / 空映射 | 单帧解析值 | 主动上报 |
| STREAM_FRAME | 原 STREAM 命令 / 调用 ID / context | 当前流帧 | 持续数据 |
| UNKNOWN_FRAME | None / "" / 空映射 | 帧解码原文 | 未匹配的完整帧 |

RAW_RECEIVE 是字节块，不保证一块是一帧。先发布 RAW_RECEIVE，再做定界/匹配；回显、半包、校验失败字节都能用于日志。解析类别和终态可能与同一批原字节有关：日志只筛选 RAW_RECEIVE，业务筛选其他类别，避免重复显示原数据。

COMMAND_RESULT 即使无设备回复也可能产生，例如 NO_REPLY 写入成功、TIMEOUT/CANCELLED。received 合并了原 command_finished 的终态语义，因此不是每条 received 都来自物理 RX；category 明确区分它们。失败消息是终态观察数据，不替代 result()/send_sync 的异常处理。

params/context 只读，业务 data 不承诺递归不可变。Message 的对象字段可以 None，值字段保持字符串/枚举/数值/集合类型。

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

### 6.5 add：对象与参数方式同时支持

```python
@overload
def add(self, spec: Spec[T], /) -> Spec[T]:
    """注册已实例化的声明，返回原对象本身。"""

@overload
def add(self, request: str, pattern: str,
        result_type: type[T] = str, *, name: str = "",
        mode: Literal[ResponseMode.SINGLE, ResponseMode.MULTI,
                      ResponseMode.STREAM] = ResponseMode.SINGLE,
        correlation: Correlation = Correlation.EXCLUSIVE,
        count: int = -1, until: str = "", error_pattern: str = "",
        timeout_s: float | Literal[TimeoutPolicy.AUTO] = 1.0,
        idle_timeout_s: float = 0.3, total_timeout_s: float = -1.0,
        idempotent: bool = False,
        parser: Callable[[str], T] | Literal[""] = "") -> Spec[T]:
    """创建并注册响应命令；request/pattern 必填。"""

@overload
def add(self, request: str, *, mode: Literal[ResponseMode.NO_REPLY],
        name: str = "", idempotent: bool = False) -> Spec[Any]: ...

@overload
def add(self, *, pattern: str, result_type: type[T] = str,
        name: str = "", parser: Callable[[str], T] | Literal[""] = "") -> Spec[T]: ...

def remove(self, spec: Spec[Any], /) -> None:
    """按身份移除；现有调用仍持有原对象，未注册对象无操作。"""
```

对象形式不混入构造字段；字段形式只构造一次。相同对象重复添加幂等，不同对象的规范化 request/事件 pattern 冲突则 raise CommandError。显示 name 不用于寻址。

```python
query = Spec("Read {channel}", r"VALUE:(?P<value>.+)", result_type=dict)
assert device.add(query) is query
version = device.add("GetVersion", r"VERSION:(?P<version>.+)", result_type=dict)
alarm = Spec(pattern=r"ALARM:(?P<code>.+)", result_type=dict)
assert device.add(alarm) is alarm
```

## 7. 主线程 add_done_callback 与 3 秒期限

### 7.1 发送与调用对象签名

```python
def send_async(self, target: Spec[T] | str, /, *,
               timeout_s: float = 3.0,
               priority: CommandPriority = CommandPriority.NORMAL,
               params: Mapping[str, object] = EMPTY_MAPPING,
               context: Mapping[str, object] = EMPTY_MAPPING) -> CommandCall[T]:
    """提交命令，返回调用对象；总期限默认 3 秒，从提交开始计时。"""

def send_sync(self, target: Spec[T] | str, /, *,
              wait_timeout_s: float = 3.0,
              priority: CommandPriority = CommandPriority.NORMAL,
              params: Mapping[str, object] = EMPTY_MAPPING,
              context: Mapping[str, object] = EMPTY_MAPPING) -> Message[T]:
    """阻塞取得成功结果；失败/超时/取消直接抛对应异常。"""

class CommandCall(Generic[T]):
    """每次异步发送的句柄；保存结果/异常/上下文，主线程交付闭包。"""
    request_id: str
    spec: Spec[T] | None                   # RAW 没有声明
    context: Mapping[str, object]
    route: SendRoute
    timeout_s: float
    deadline: float                       # monotonic 绝对截止时间

    def add_done_callback(self, callback: Callable[[Message[T]], None], /) -> CommandCall[T]:
        """注册只接收一个 Message 的函数；仅在期限内于应用主线程调用。"""

    def result(self) -> Message[T]:
        """成功则返回结果；已失败重抛异常；未完成抛 CommandNotReadyError。"""

    def cancel(self) -> None:
        """请求本地取消，取消后不再执行未交付的回调。"""

    @property
    def done(self) -> bool:
        """是否已经保存成功结果或异常。"""

    @property
    def callback_expired(self) -> bool:
        """是否因截止时间到达而不再接受/派发闭包。"""

    @property
    def exception(self) -> Exception | None:
        """本次命令的失败异常对象；尚无异常为 None。"""

    @property
    def callback_exception(self) -> Exception | None:
        """闭包自身最后一次异常；无异常为 None。"""
```

CommandCall 是普通 Python 句柄，主线程投递由门面内部 QObject 桥接器负责，不再为每次调用建立独立 QObject/finished/callback_error 信号。这降低跨线程创建/销毁 QObject 的负担。

目标为 Spec[T] 时返回对应 T，字符串目标静态类型为 Any；实际静态重载须分别声明。EMPTY_MAPPING 是共享空只读映射，不用可变 {} 默认值；params/context 发送时生成浅快照。

本轮不保留 then、on_finished、receiver 和 per-call finished。用户的最小写法就是：

```python
device.send_async(query, timeout_s=3.0).add_done_callback(
    lambda result: self.update_row(result.context["row_id"], result.data)
)
```

### 7.2 时间限制：不重启计时，超时不回调

- send_async 的 timeout_s 默认 3.0，必须有限正数；t0 为提交时刻，deadline=t0+timeout_s。包含排队、发送、响应和主线程回调交付时间，不是仅从开始 read 计时。
- add_done_callback 只接收函数，不设置第二个冲突超时；沿用本次发送的 deadline。较晚注册不会重启 3 秒计时。
- Spec 的首帧/帧间/协议总时限仍生效，与发送总期限取先到者；例如默认 Spec.timeout_s=1.0 可能先于 3 秒产生命令超时。调用者如希望等待完整 3 秒，应配置相应响应等待策略。
- 完成在先、订阅在后时，只要未到 deadline，成功结果仍可在主线程排队交付；没有“信号已经发射所以闭包漏掉”的问题。
- 仅成功结果进入闭包；超时、取消、执行错误不执行闭包，result() 可重新抛出对应异常。timeout_s 到期且命令仍未完成时，终态为 TIMEOUT，移除队列/等待，释放未交付订阅。
- 即使响应在期限内到达，主线程堵塞到 deadline 之后才处理回调，也不再调用。此时命令仍是成功，result() 可以取到成功数据，但 callback_expired=True；不能把已经成功的物理操作改成 TIMEOUT。
- 在 deadline 到达后新注册闭包无操作并返回自身；失败/取消的调用也不执行新闭包。明显不接受一个位置参数的函数在注册处直接 raise TypeError；可调用对象无法静态取得签名时，执行边界仍验证其实际调用，不能承诺仅靠 inspect 就覆盖所有可调用对象。
- 以 monotonic 时钟比较，回调执行前再次验证状态和截止时间，Qt 定时器只作唤醒/清理手段。迟到结果不复活过期订阅。
- 一旦回调已在 deadline 之前开始执行，库不能抢占中断函数；3 秒是启动资格期限，不是执行函数的强制 CPU 时限。
- STREAM 没有自然完成时刻，仍只允许流帧通过 received 观察；正常流数据不触发 add_done_callback。默认发送总期限仍适用，用户明确传较长 timeout_s；如何主动“成功结束流”属于未来协议能力。

### 7.3 明确保证应用主线程

SerialForge 必须在 QCoreApplication.instance().thread() 中创建且保持该线程归属，构造时错误线程直接 raise；application="core" 首次创建应用时也必须位于 Python 主线程，不能在应用工作线程偷偷建立主应用。内部桥接 QObject 固定在应用主线程，通过 QueuedConnection 派发闭包。不会因为 send_async 在哪个线程调用就改变回调线程；send_async/add_done_callback 可以来自应用工作线程。

- 回调不是 Slot 也能使用，参数只有一个 Message。
- 永远排队，不在发送/注册处内联执行；工作线程不能直接调用用户闭包。
- 主线程运行 Qt 事件循环是必要条件。纯同步 core 脚本无 exec 时使用 send_sync，异步回调不会暗中启动主循环。
- 可多次注册，每次注册建立一次独立订阅，主线程按注册顺序派发；派发每个订阅前检查 deadline。
- pending 和派发期间库保持必要引用，临时链式对象不会提前消失；完成/失败/超时/关闭后释放闭包引用。
- 已经成功但尚未派发的调用执行 cancel() 时，只撤销剩余闭包，不将已保存的成功结果改为 CANCELLED；尚未完成的取消才产生取消终态。
- 不再要求 receiver 参数；库无法自动知道闭包捕获了哪个界面对象。界面销毁时应用取消关联 CommandCall，派发期间仍需检查目标有效性。取消会阻止尚未开始的闭包，不能中断正在执行的闭包。
- 闭包自身异常存入 callback_exception，再在主线程执行边界抛出，遵循宿主 Qt 绑定的异常处理机制；不改变原串口成功结果、不新增错误信号、不私自安装全局 sys.excepthook。
- 原子完成状态/订阅/过期清理共用锁，公开通知和用户函数都在锁外执行。

Qt queued 投递在接收对象线程执行，见 [Qt QObject 官方说明](https://doc.qt.io/qtforpython-6.11/PySide6/QtCore/QObject.html)。这里使用内部主线程桥接，而非假定普通 lambda 自带主线程上下文。

### 7.4 异常传播：删除 error_occurred 后的完整契约

后台线程 raise 不会回到已经返回的 send_async 调用点。Python 的独立线程异常有自己的处理边界，见 [Python threading 文档](https://docs.python.org/3/library/threading.html)。必须先保存异常并保持调用资源可清理，再让 result() 重抛；不能让工作线程直接崩溃后丢失所有在途调用。

| 场景 | 传播方式 | 是否执行 add_done_callback |
| --- | --- | --- |
| 参数/配置/未注册声明错误 | send/configure/add 等调用点直接 raise | 否，无有效调用 |
| scan/connect/disconnect 内部错误 | 阻塞方法等待内部线程并在调用线程 raise | 不适用 |
| 同步设备报错/解析失败/断线/忙/超时/取消 | send_sync 直接 raise 对应异常 | 不适用 |
| 异步命令错误/超时/取消 | 调用对象保存异常，result() 重抛 | 否 |
| 尚未完成时读取 result() | CommandNotReadyError，不阻塞 | 不改变已注册闭包 |
| 成功且尚未到 deadline | result() 返回 Message | 是，在主线程 |
| 主动接收/日志后台故障，无对应调用 | 门面错误队列，check_errors() 读取时 raise | 不虚构命令回调 |
| 闭包业务代码异常 | 主线程边界抛出，callback_exception 保存 | 函数已经开始，后续独立订阅按期限处理 |

异常使用 serialforge.errors 类型并保留 cause/request_id/context；需要定义明确的超时、取消、未完成异常类。取消/DEVICE_ERROR 等原状态仍可保留在内部终态 Message 和统一观察信号，异常接口不再伪装成正常返回值。

```python
def check_errors(self) -> None:
    """取出并抛出一个无所属调用的后台异常；无待处理异常时正常返回。"""
```

check_errors 不重复抛出已归属 CommandCall 的命令异常；错误队列应有明确上限与溢出计数，不无限持有 traceback。长时间不读取的异常可保存结构化描述，读取时构造公共异常并保留必要 cause 信息，避免 traceback 保留整个工作线程对象图。

如果希望在 GUI 中统一处理所有后台异常，应用可用自己的主线程定时器检查 call.done/result()/device.check_errors()，在同一 try/except 内处理。仅注册成功闭包且从不检查错误，无法自动得到失败通知；这是“超时不回调且没有错误信号”的明确接口取舍，不承诺异步异常自动穿越线程。

同步无需主事件泵；应用工作线程有 Qt 事件循环仍可调用。GUI 主线程同步会暂停响应。库内部读写/调度线程与直接回调上下文在提交前拒绝 send_sync。保留迟到回复隔离、排他/标签关联和有限驱动写超时。

### 7.5 context：按每次调用关联

params 用于请求模板及既有协议关联，context 仅是本地业务字典，不写入串口、不参与匹配。匹配先确定 request_id，再取上下文；不能按 Spec/name 或“最近一次发送”猜测。

发送时浅复制顶层并包装为只读映射。CommandCall.context、该调用的 Message/RESPONSE_FRAME/STREAM_FRAME.context 共用快照；发送后修改原 dict 顶层不影响结果，嵌套值不递归复制。主动事件、未知帧、RAW_RECEIVE 没有调用上下文，使用空映射。

所有成功/失败/取消/超时及 RAW 调用都保留上下文，异步失败从异常或调用对象取得；重连重试记录也保留，原重试 request_id 语义另行沿用。默认不写入日志或错误字符串。应用持有调用对象/结果时，其字典中的对象也会保持存活。

## 8. 信号收敛与 traffic_logged 说明

### 8.1 门面信号名单

```python
class SerialForge(QObject):
    connection_state_changed = Signal(object)  # ConnectionState
    received = Signal(object)                  # Message[Any]
    raw_sent = Signal(bytes)                   # 实际写入串口的原始字节
```

- received 合并原 command_finished/event_received，并加入 RAW_RECEIVE 原字节观察。COMMAND_RESULT 每次调用恰一次；RESPONSE_FRAME/事件/流帧按解析语义发布；RAW_RECEIVE 每次非空后台 read 一次。
- raw_sent 是软件实际发送的 bytes，含编码结果、发送结束符、校验等；在后端确认接受字节后发，不在排队时发。部分写入只发送实际已接受的前缀，再抛写入异常；无法确定驱动是否接受时不伪造成功发送。
- 所有扫描探测、初始化、心跳、重试、RAW 和业务命令的实际串口写入也纳入 raw_sent。扫描临时端口的数据同样纳入 RAW_RECEIVE；并行扫描下裸 bytes 无端口信息，跨端口归属不保证，若以后需要精确多端口日志须另评审带端口元信息的发送载荷。
- 门面内部主线程桥接统一派发 received/raw_sent，普通闭包消费也在主线程；信号记录包含观察时间而非保证纳秒级物理线序，多端口并发没有全局物理先后顺序。
- 物理接收字节和原始发送信号独立于日志配置，不能为了启用滚动控件而被迫开启文件/loguru 输出。
- 删除 scan_finished、scan_progress、error_occurred、traffic_logged，不提供隐含的第二套完成/错误信号。扫描由应用自建工作线程调用 scan，获取返回列表或捕获异常。
- 主线程事件循环未运行时，queued 观察不保证即时交付；同步 API 仍可独立工作。

原字节观察在库侧不节流/截断/合并，界面端应自行批量滚动显示。传输/调度线程不得因 GUI 显示阻塞；高吞吐场景需验证 Qt 事件队列内存，用户自行降低采样频率不等于库可以默默丢失原始观察。若以后加入有损限流必须显式配置并提供丢弃计数。

### 8.2 traffic_logged 当前到底做什么

根据实际 TrafficLogger 源码：

1. TX/RX 会构造 TrafficRecord，字段为 direction、timestamp（Unix 秒）、port、data、route、spec。
2. 收发记录保存在有限 recent_records 环形缓存，并通过 traffic_logged 通知观察者；源码先发这个信号，再检查 enabled，所以它不受文件/loguru 日志开关控制。
3. 启用日志时另格式化为时间、方向、端口、HEX、转义文本，并以 loguru DEBUG 输出；文件策略独立控制。
4. 流 RX 通知有按间隔合并的机制，并非原始完整字节订阅；生命周期 EVT 写日志，但当前 record_event 不通过 traffic_logged 发出，也不进入流量环形缓存。TrafficRecord 支持 EVT 字段不代表该信号实际发送 EVT。

因此它原本是“带元信息的收发记录观察”，不负责命令结果回调。此前文档把信号说成“仅启用日志时才发送”不准确，本轮已修正。

**用户已确认：从公开 API 删除 traffic_logged，不再作为待选方案。** SerialForge 不定义或转发此信号，顶层和 advanced 也不提供替代访问入口；日常 API 文档、补全和示例不展示它。上述说明仅记录旧实现背景，供迁移时参考。

received/raw_sent 是用户观察 RX/TX 的统一入口。内部环形缓存和 loguru/文件输出继续保留为私有实现；若内部仍需要流量通知，采用私有名称，不通过门面暴露。TrafficRecord 不作为本轮新增的用户交互概念，既有进阶类型的兼容去留按迁移名单处理。

### 8.3 其他门面能力

```python
def set_log_config(self, config: LogConfig) -> None:
    """更新内部日志策略，不控制 received/raw_sent 是否发布。"""

def stats(self) -> HandlerStats:
    """返回延迟、调度与内部诊断计数快照。"""
```

## 9. 更新后的最小示例

### 9.1 同步与已有 Spec

```python
from serialforge import SerialForge, Spec, SerialForgeError

device = SerialForge(application="core")
query = Spec("Read {channel}", r"VALUE:(?P<value>.+)", result_type=dict)
assert device.add(query) is query
devices = device.scan(baudrates=[115200], probe="GetVersion",
                      probe_pattern=r"VERSION:(?P<version>.+)")
if not devices:
    raise RuntimeError("未找到设备")
try:
    device.connect(devices[0])
    result = device.send_sync(
        query, params={"channel": 1}, context={"row_id": 42},
    )
    print(result.data["value"], result.context["row_id"])
except SerialForgeError as exc:
    print(exc)
finally:
    device.disconnect()
```

示例中的业务命令仅用于文档/测试。响应解析需要命名组字典，因此显式 result_type=dict；默认返回 str 时可以直接显示完整响应文本。

### 9.2 主线程闭包，默认 3 秒不超时才执行

应用已建立 Qt 主循环、完成连接，以下方法属于应用现有 ViewModel：

```python
def refresh_row(self, row_id: int, channel: int) -> CommandCall[dict]:
    """异步读取通道；成功且期限内执行主线程界面更新。"""
    def consume(result: Message[dict]) -> None:
        self.update_row(result.context["row_id"], result.data["value"])

    return device.send_async(
        query,
        timeout_s=3.0,  # 省略也为 3 秒
        params={"channel": channel},
        context={"row_id": row_id},
    ).add_done_callback(consume)

call = self.refresh_row(42, 1)
# 应用自行保留 call；主线程定时检查，捕获失败异常：
def check_call() -> None:
    if call.done:
        try:
            call.result()
        except SerialForgeError as exc:
            self.show_error(exc)
```

consume 只接收 Message，并在应用主线程执行；不需 receiver 参数。超时/失败/取消不调用 consume。若界面被销毁，应用取消该 call。闭包捕获界面对象的生命周期由应用管理。

### 9.3 两个信号实现滚动日志

```python
def append_rx(message: Message[Any]) -> None:
    if message.category is MessageCategory.RAW_RECEIVE:
        self.rx_log.append(message.raw_data.hex(" "))

def append_tx(data: bytes) -> None:
    self.tx_log.append(data.hex(" "))

device.received.connect(append_rx)
device.raw_sent.connect(append_tx)
# 已解析结果也从 received 观察，按 category 选择业务分支；
# 原始日志只处理 RAW_RECEIVE，不再重复打印解析结果。
```

库保证这些门面观察信号在应用主线程派发；应用仍需正确持有门面、运行主事件循环和管理界面生命周期。

## 10. QtPy、多绑定与 Python 版本

### 10.1 区分三个兼容问题

| 实际目标 | QtPy 能否解决 | 推荐范围 |
| --- | --- | --- |
| PySide6/PyQt6 | 能统一主要绑定 API | 首期候选，验收后承诺 |
| PyQt5/PySide2 | 有适配，但不等于本库可运行 | 后续单独验收 |
| 更低 Python 版本 | 不能直接解决 | 源码语法、依赖 wheel、软件矩阵共同决定，首期仍 >=3.11 |
| 完全不依赖 Qt | 不能，仍需一种 Qt 绑定 | 独立纯 Python 核心属于更大重构，不纳入本次 |

现有架构记录通过 Python 3.11–3.14 软件矩阵，但属于 PySide6 基线，不能视为 PyQt6 或 Qt5 的通过结果。

### 10.2 依赖选型

建议评估 QtPy（MIT），集中在内部 QtCore 适配层导入 QObject/QThread/QCoreApplication/Signal/Slot/Qt。它支持四种绑定、提供 Signal/Slot 等别名，且不自行安装 Qt 绑定，见 [QtPy 官方仓库](https://github.com/spyder-ide/qtpy)。

| 项目 | 收益或代价 |
| --- | --- |
| 收益 | 复用绑定名称/枚举/选择机制，减少重复适配 |
| 新依赖 | QtPy 与解析后的依赖；候选版本、传递依赖数、wheel 大小需获准后核实 |
| 绑定体积 | 仍需一个 Qt 绑定及其 Qt DLL，QtPy 不替代它 |
| 自研替代 | Qt6 限定的窄导入层较小，但线程/元对象/销毁行为仍需完整验证 |
| 未验证 | Windows/Python wheel 矩阵、QtCore 导入链是否意外加载 QtGui/QtWidgets、宿主分发许可证条件 |

本次只查官方资料，没有安装依赖、解析锁文件或运行绑定验证。如果 QtPy 的 QtCore 导入链违反禁止 QtGui/QtWidgets 的硬约束，改选窄 Qt6 层，不自动放宽规则。

推荐核心依赖 QtPy/pyserial/loguru，pyside6 extra 提供 PySide6-Essentials，pyqt6 extra 提供 PyQt6。用户分别使用 `uv add "serialforge[pyside6]"` 或 `uv add "serialforge[pyqt6]"`。这会改变当前普通安装自动带 PySide6 的体验，需要迁移说明；继续默认捆绑 PySide6 会让 PyQt 用户下载多余绑定，因此不推荐。最终版本范围以 Windows 实测为准。

### 10.3 选择与验证

宿主在首次 Qt 导入前设置 QT_API=pyside6/pyqt6，或复用已加载的受支持绑定。库不覆盖环境变量、不热切换绑定；发现不一致明确报错。多绑定环境要求明确选择，不依赖默认优先级。

queued/auto 连接会在接收 QObject 的线程上下文执行，见 [Qt 官方信号槽说明](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html)。因此同步返回必须走独立完成状态，不能依赖发往被阻塞线程的 queued 槽。

逐绑定验证 Signal(object) 身份、Slot 接收线程、connect/disconnect 名称冲突、枚举、异常、QThread 回收、应用退出、导入边界、无 exec 同步模式。不同绑定在独立环境/进程运行，不能在同一进程切换。

## 11. 导出与迁移计划

推荐日常顶层：SerialForge、Spec、Message、MessageCategory、DeviceInfo、CommandStatus、ConnectionState、ResponseMode、Correlation、ScanMode、SerialForgeError。取消固定 15 名字约束。

CommandCall 自动返回，具名导出在 advanced；DeviceProfile/DeviceFinder/LogConfig 和诊断配置也在 advanced。定义层不反向导入实现模块。

| 旧入口/行为 | 新入口/行为 | 注意 |
| --- | --- | --- |
| SerialHandler | SerialForge | 门面现在要求应用主线程创建 |
| CommandSpec/EventSpec | Spec | 旧声明兼容适配保留身份；新默认 result_type=str |
| register/unregister | add/remove | add 同时支持对象/字段 |
| send / CommandTicket | send_async / CommandCall | 新 result() 读取失败时 raise；旧票据语义仅按迁移层提供 |
| send_and_wait | send_sync | 默认等待 3 秒；失败改为 raise |
| command_finished/event_received | received | 分类统一，原始 RX 和本地调用终态需区分 |
| traffic_logged | received/raw_sent | 已确定删除公开信号，不提供公开兼容别名；内部诊断通知私有化 |
| scan_finished/scan_progress | scan 返回值 | 用户自行开工作线程 |
| error_occurred | 调用处 raise、call.result()、check_errors() | 不再提供错误信号 |
| None 值参数 | -1/""/枚举 NONE/真实默认 | 迁移层可转旧值，新的 Spec 不接收 None |

本轮属于破坏性 API 调整，不能承诺旧信号消费者完全不改。可以保留一个迁移周期的旧构造/发送入口，但最终 SerialForge 不重新暴露用户已删除的信号。旧结果类型判断、默认解析类型、失败返回方式、超时闭包抑制和主线程归属都要写迁移说明。

不执行发布、push 或合并。静态快照同时覆盖最终导出与获准保留的迁移入口。

## 12. 实施分期与验收

不重写已交付的 M1→M7 历史。改造使用独立 R1→R5；每阶段实现、测试、汇报后等待用户确认，现有 M7 硬件缺项仍保留。

| 阶段 | 范围 | 验收 |
| --- | --- | --- |
| R1 契约 | 设计补充、规则/导出预算、迁移、依赖选择 | 签名和不变量明确，确定 QtPy 或窄适配层 |
| R2 调用 | 完成状态、CommandCall/add_done_callback、3 秒期限、异常/context | 主线程期限内交付；超时不回调；result 重抛；上下文不串 |
| R3 模型 | Spec/Message、解析/注册/调度及旧声明入口 | 全链路身份；category 字段不变量；帧路由语义保持 |
| R4 门面 | 主线程门面、add/scan、received/raw_sent、core 生命周期 | 返回扫描结果；日志信号含原始字节；删除信号不再存在 |
| R5 交付 | Qt6 双绑定、extras、示例、文档、软件矩阵 | 独立环境/wheel 通过，导入仅 QtCore，记录硬件未验证 |

预计涉及 models/advanced/顶层导出、connection 注册/解析/调度/handler/pending、discovery 门面、内部 Qt 适配、pyproject/uv.lock、测试/示例/使用文档/规范/API 快照。不重写帧定界和校验算法，不把测试后端移进源码。

关键验证：

1. 所有 Spec 值字段类型/哨兵明确，默认 SINGLE/str；count=-1、parser=""、total_timeout_s=-1；对象/字段 add 均保留身份。
2. 主线程/工作线程发起异步都在应用主线程回调；闭包仅一个 Message 参数；临时句柄保持存活。
3. 3 秒从提交计时：排队到期不写；快速完成后订阅不漏；迟到注册/迟到派发/失败/取消不调用闭包；回调已开始不被强制中断。
4. 主循环堵塞但命令成功时，result 仍成功而回调过期；过期订阅及时释放；多次注册独立交付。
5. scan/connect/send_sync 直接 raise；异步 result 重抛；无所属后台异常从 check_errors 读取；无 error_occurred。
6. received 分类准确、RAW_RECEIVE 覆盖半包/回显/损坏字节；RAW_RECEIVE 与解析通知不会在示例日志中重复。
7. raw_sent 是确认实际写入的字节，覆盖部分写、心跳、初始化、探测和重试；排队拒绝不虚构 TX。
8. scan_finished/scan_progress/command_finished/event_received/traffic_logged 等旧门面信号不再存在；traffic_logged 在顶层、advanced、门面、日常文档和示例均无公开入口，日志 enabled 不关闭收发观察。
9. 相同 Spec 并发 context 隔离，失败异常和结果保留上下文；主动事件/原字节不错误绑定业务。
10. 无 exec 同步流程、关闭线程/取消/迟到帧隔离、配置替换失败和资源回收均通过。
11. 双绑定逐环境、仅 QtCore、Python 有效组合、类型检查与干净 wheel 验收；记录主线程高吞吐原始信号队列内存和 Windows/芯片未验证项。

## 13. 本次交付与后续入口

本轮仅修订此 explore 文档，未修改源码、测试、依赖和现有其他改动；没有功能实现，因此未运行库功能测试。外部资料核对不是运行兼容性验证。

只有用户明确说“开始修改代码”才进入实施阶段；在此之前持续修订文档，即使已确认部分方案也不能开工。届时再用项目工具登记 todo/checkpoint、协调 M7 激活任务、保存设计契约并按规则归档此文档。不创建第二个激活任务，不提交其他会话已有改动。
