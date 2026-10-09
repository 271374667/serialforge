# 0009_CommandCall主线程回调与异常契约

> 分类: knowledge/software | 创建: 2026-10-09 | 适用范围: 每次调用、主线程回调、超时和异常
> 状态: R1 已批准的目标契约；R2–R5 未实现，当前运行 API 仍为旧基线

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

异常位于 serialforge.errors：CommandExecutionError(CommandError) 统一持有 request_id/spec/context/status/sent/raw_frames，不将 context 自动拼进错误文本。TIMEOUT→CommandTimeoutError、CANCELLED→CommandCancelledError、BUSY→CommandBusyError、DISCONNECTED→CommandDisconnectedError、PARSE_ERROR→CommandParseError、DEVICE_ERROR→DeviceCommandError，均继承 CommandExecutionError；未完成读取为 CommandNotReadyError(CommandError)。保留可用 cause，不打印日志或吞掉异常。取消/DEVICE_ERROR 等原状态仍可保留在内部终态 Message 和统一观察信号，异常接口不再伪装成正常返回值。

```python
def check_errors(self) -> None:
    """取出并抛出一个无所属调用的后台异常；无待处理异常时正常返回。"""
```

check_errors 不重复抛出已归属 CommandCall 的命令异常；错误队列固定上限 128，溢出淘汰最旧描述并累计 stats().dropped_background_errors，不无限持有 traceback。长时间不读取的异常可保存结构化描述，读取时构造公共异常并保留必要 cause 信息，避免 traceback 保留整个工作线程对象图。

如果希望在 GUI 中统一处理所有后台异常，应用可用自己的主线程定时器检查 call.done/result()/device.check_errors()，在同一 try/except 内处理。仅注册成功闭包且从不检查错误，无法自动得到失败通知；这是“超时不回调且没有错误信号”的明确接口取舍，不承诺异步异常自动穿越线程。

同步无需主事件泵；应用工作线程有 Qt 事件循环仍可调用。GUI 主线程同步会暂停响应。库内部读写/调度线程与直接回调上下文在提交前拒绝 send_sync。保留迟到回复隔离、排他/标签关联和有限驱动写超时。

### 7.5 context：按每次调用关联

params 用于请求模板及既有协议关联，context 仅是本地业务字典，不写入串口、不参与匹配。匹配先确定 request_id，再取上下文；不能按 Spec/name 或“最近一次发送”猜测。

发送时浅复制顶层并包装为只读映射。CommandCall.context、该调用的 Message/RESPONSE_FRAME/STREAM_FRAME.context 共用快照；发送后修改原 dict 顶层不影响结果，嵌套值不递归复制。主动事件、未知帧、RAW_RECEIVE 没有调用上下文，使用空映射。

所有成功/失败/取消/超时及 RAW 调用都保留上下文，异步失败从异常或调用对象取得；重连重试记录也保留，原重试 request_id 语义另行沿用。默认不写入日志或错误字符串。应用持有调用对象/结果时，其字典中的对象也会保持存活。
