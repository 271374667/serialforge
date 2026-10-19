# 使用指南

> 适用版本: 0.0.1 | SerialForge 公共 API 改造

## 推荐用法速览

写业务代码时按这三步组织，本页其余内容都是它们的细节：

```python
# ① 声明集中一处：每个命令只创建一次，之后只引用对象
self.VERSION = self.forge.add(request=">SN_INFO_GET=?",
                              pattern=r">SN_INFO_GET=(?!\?)(?P<sn>.+)")
self.STATUS = self.forge.add(request=">STATUS=?",
                             pattern=r"(?:IDLE|A2DP|HFP)")

from serialforge import Message

# ② 发送与消费写在同一个函数里：闭包在主线程，直接改界面
def on_get_status(self):
    def on_done(message: Message[str]) -> None:
        self.status_label.setText(str(message.data))

    self.forge.send_async(self.STATUS).add_done_callback(on_done)

# ③ 扫描即配置，连接用扫描结果
devices = self.forge.scan_sync(baudrates=[921600], probe=self.VERSION)
if devices:
    self.forge.connect_sync(devices[0])
```

目标是「命令表 ↔ 代码骨架」一一对应：加命令只改声明区，发送处只引用对象，
结果归属用 `message.spec is self.STATUS` 判断，不依赖 name 或字符串匹配。

## 声明和注册

统一 `Spec` 是 `frozen=True`、`eq=False`，注册、发送、结果保留原对象身份。
`add` 支持对象和字段两种方式，重复添加同一对象幂等，不同对象的请求/事件模式冲突会 raise。

```python
from serialforge import Spec, ResponseMode

query = Spec("Read {channel}", r"VALUE:(?P<channel>.+):(?P<value>.+)", dict)
assert forge.add(query) is query
many = forge.add("ReadAll", r"VALUE:(?P<value>.+)", dict,
                 mode=ResponseMode.MULTI, count=3)
alarm = forge.add(pattern=r"ALARM:(?P<code>.+)", result_type=dict)
stop = forge.add("Stop", mode=ResponseMode.NO_REPLY)
```

响应 request/pattern 必填，事件 pattern 必填；不填 mode 为 SINGLE。
MULTI 显式 `mode` 且 `count>0` / `until` 非空二选一，STREAM 显式模式并需要 pattern。
默认 `str` 返回整段匹配帧；`dict` 返回命名组字典，`int`/`float` 取第一个捕获组转标量，
`dataclass` 按命名字段构造。`parser` 接收匹配帧文本，优先于内置解析，返回值必须符合 `result_type`。
解析器在 I/O 路径只负责数据转换；界面操作放在主线程 `add_done_callback` 里。
`count=-1`、`until/error_pattern/parser=""`、`total_timeout_s=-1.0` 禁用对应规则，不能传 `None`。

## 发送与元数据

```python
message = forge.send_sync(query, params={"channel": 1}, context={"row": 7})
call = forge.send_async(query, params={"channel": 2}, context={"row": 8})
call.add_done_callback(lambda message: print(message.data, message.context["row"]))
```

`send_sync(target, wait_timeout_s=3.0, ...)` 与 `send_async(target, timeout_s=3.0, ...)` 同级。
`params` 只用于请求占位符和协议关联；`context` 独立保存为只读浅快照，不上线路，
同一声明并发发送也不会串数据。target 为完整文本时先按注册表匹配，否则默认原文发送；
`allow_raw_text=False` 拒绝未注册文本。文本方式不能另传 params，禁止注入控制字符。

调用期限默认 3 秒，从提交开始计时。命令自身的协议预算是另外两层：
`Spec.timeout_s`（默认 1 秒）是首次回复等待上限，`total_timeout_s`（默认关闭）是额外总上限。
多行或慢响应的调用期限必须大于协议预算，例如声明 `total_timeout_s=5.0`，调用时给 `timeout_s=6.0`。
排队超时的命令不会写出；已写出的独占命令超时后隔离迟到帧。

## 闭包消费

`CommandCall.add_done_callback(callback)` 接收一个只带 `Message` 参数的函数，返回自身。
可检查且明显不能接收一个位置参数的函数在注册时直接抛 `TypeError`。
回调始终排队到 Qt 主线程，多次注册按顺序执行，成功后期限内补注册也会排队回放。
结果已成功但主线程过期才恢复时，保留成功 result，取消回调；超时不回调。
可读 `done`、`callback_expired`、`exception`、`callback_exception`；`result()` 不阻塞。
未完成查询抛 `CommandNotReadyError`，失败重抛保存的执行异常。回调自身异常交由
宿主 Qt 的异常处理边界报告，同时保留 `callback_exception`，不安装全局异常钩子。
界面关闭时 `cancel()`；成功后取消只抑制回调，不改变成功结果。

回调参数按该命令**声明的 `result_type`** 标注 `Message[T]`：默认 `str` → `Message[str]`，
`result_type=int` → `Message[int]`，`dict`/dataclass 按声明类型，一个回调服务多条命令或处理 `received`
时用 `Message[Any]`。`Message.data` 始终是 `list[T]`：单条响应是单元素列表，多条响应保留完整列表，
无回复是空列表，因此 MULTI 和 SINGLE 都直接写 `Message[str]` 并读取列表，不需要额外收窄。

超时或失败不会触发成功闭包，所以「失败也要给用户反馈」时用 `call.result()` 取异常，
或像多行命令那样用定时器兜底：

```python
def on_reset(self):
    self._reset_done = False

    def on_done(message: Message[str]) -> None:
        """三行齐全才算成功：data 始终是行列表。"""
        self._reset_done = True
        lines = message.data
        self.output.setText(
            "OK" if set(lines) == {"OK", "SYS_START", "IDLE"} else "Failed"
        )

    call = self.forge.send_async(self.RESET, timeout_s=6.0)  # 大于声明的 5 秒总预算
    call.add_done_callback(on_done)
    QTimer.singleShot(6100, self._reset_timeout)             # 超时/失败不回调，定时器兜底

def _reset_timeout(self):
    if not self._reset_done:
        self.output.setText("Failed")
```

## 扫描与配置

`configure`/`scan_sync` 支持 `DeviceProfile` 或必填 `baudrates`/`probe` 字段。文本 probe 必须再给
`probe_pattern`，`probe_timeout_s` 默认 1 秒；对象 probe 不接受这两个附加参数。
已注册的声明对象可直接当探针，但必须 `SINGLE`、无占位符，且 pattern 不能匹配自己的请求回显。
`vid_pid=()` 扫描全部串口，`heartbeat_s=-1.0` 禁用心跳，`port=""` 不限制端口。
配置只在断开且无扫描、连接或待发送工作时原子替换；已注册声明保留身份。

`scan_sync` 阻塞返回 `list[DeviceInfo]`，取消后等待探测句柄关闭；不自动连接、不发扫描信号。
无命中返回 `[]`：个别端口打不开、写不进或没有应答只跳过该端口，不会让整次扫描失败，也不抛库异常。
探测按端口并行、同一端口波特率串行：端口只打开一次，后续速率在原句柄上切换。打开调用默认限时
1 秒（`RuntimeConfig.probe_open_timeout_s`），驱动卡死时放弃该端口并在底层返回后关闭迟到句柄；
每个速率先用 `probe_activity_window_s`（默认 0.3 秒，`0` 关闭）观察是否收到非回显字节，没有字节
就立即换下一个速率，有字节才等满该探针的 `timeout_s`。活性筛选只对“波特率错了也会回包”的设备
有加速效果，慢启动设备请调大窗口或设 `0` 关闭。
`ScanMode.ALL` 扫完全部，`FIRST_MATCH` 拿到第一个成功者即取消其余探测；`cancel_scan()` 仅请求取消。
`connect_sync(DeviceInfo)` 用扫描已确认的波特率直接打开并跳过重新探测；`connect_sync("COM11")` 绕过 USB
身份过滤但仍探测；`connect_sync()` 按保存策略发现首个设备。每次 `connect_sync`/`scan_sync`/`scan_async`
都会先完整断开旧会话，因此已连接时也能直接重扫。

界面应用可直接用 `scan_async()`：它在后台线程扫描，闭包在主线程收到设备列表，不阻塞界面。

```python
call = forge.scan_async(baudrates=[921600], probe=PROBE)
call.add_done_callback(lambda devices: connect_first(devices))
# 失败不执行成功闭包，用 call.result() 重新抛出异常
# 需要中止时 call.cancel()：抑制回调，result() 返回已完成的匹配
```

`ScanCall` 从 `serialforge.advanced` 导入；扫描没有额外的 3 秒投递期限，由 `probe_total_timeout_s`
与 `probe_open_timeout_s` 兜底。`connect_async()` 同风格返回 `ConnectCall`，闭包在主线程收到已连接的
`DeviceInfo`；`connect_sync()` 仍阻塞，界面应用要把它放到自己的工作线程。同步 `scan_sync()` 也没有
进度信号。

## 生命周期与迁移

在主线程创建门面；`add`/`send` 支持应用线程调用。`connect_sync`/`scan_sync`/`send_sync` 阻塞调用者；
`disconnect` 取消工作并等待 I/O/扫描结束，支持重复调用，也连接 `aboutToQuit`。
兼容 `SerialHandler` 只桥接旧方法名；其旧信号也已移除。旧声明构造仍保留旧解析/模式推断，
新 `Spec` 使用本页规则；新结果统一 `Message`。完整签名见 [API 参考](api.md)。

## 统一接收与原始收发

`received(Message)` 用 `category` 区分：

| category | 内容 |
| --- | --- |
| RAW_RECEIVE | 真实 read 的 raw_data，含结束符、回显和不完整帧 |
| RESPONSE_FRAME | 单次/多次命令已匹配的帧和解析数据 |
| COMMAND_RESULT | 每次调用唯一终态，含本地超时/取消等失败 |
| DEVICE_EVENT | 已注册设备主动事件 |
| STREAM_FRAME | 流式调用帧和该调用 context |
| UNKNOWN_FRAME | 无匹配声明的完整帧 |

`raw_sent(bytes)` 是实际成功写入的字节，包括部分写入前缀以及探测、初始化、心跳、重试。
日志控件仅收 `RAW_RECEIVE.raw_data` 与 `raw_sent`，避免重复记录解析帧和终态。
两个信号都在主线程投递，独立于私有日志开关；并行扫描的裸 TX bytes 没有端口字段。
门面不公开 `traffic_logged`、`error_occurred`、`command_finished` 或 `event_received`。

## 接收定界

发送 terminator 与接收 framing 相互独立。无结束符设备使用：

```python
from serialforge.advanced import FramingConfig, FramingMode

forge.configure(baudrates=[115200], probe=PROBE,
                framing=FramingConfig(mode=FramingMode.SILENCE_GAP,
                                      silence_gap_s=0.08))
```

## 错误与日志

同步失败 raise，异步失败从 `call.result()` 重抛；异常含 `request_id`/`spec`/`context`/`status`/
`sent`/`raw_frames`。未归属命令的后台故障用 `check_errors()` 在调用线程取出并抛出，
缓冲最多 128 个，溢出计数见 `stats().dropped_background_errors`。
库内只用默认静默的 loguru DEBUG。`advanced.LogConfig` 可以开启连接文件日志，
文件上限、保留数量和路径沿用原策略，与观察信号无关。
