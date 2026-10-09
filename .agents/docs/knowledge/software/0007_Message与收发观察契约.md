# 0007_Message与收发观察契约

> 分类: knowledge/software | 创建: 2026-10-09 | 适用范围: Message、received/raw_sent、私有诊断
> 状态: R2–R4 已接入源码；R5 双绑定与完整矩阵验收进行中

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
