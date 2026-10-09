# 0006_Spec声明与add重载契约

> 分类: knowledge/software | 创建: 2026-10-09 | 适用范围: 声明、注册、解析
> 状态: R1 已批准的目标契约；R2–R5 未实现，当前运行 API 仍为旧基线

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
