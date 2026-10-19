# 声明与响应模式

> 声明就是替你的上位机起草一份“协议契约”：我发什么指令、你回什么规则、收到后怎么自动打包成 Python 对象。

在传统的串口代码里，我们经常看到这样的场景：几十个不同功能的地方散落着类似 `ser.write(b"READ\r\n")`，紧接着后面跟着几行 `ser.readline().decode().strip().split(",")`。一旦下位机固件改了一个字母，满屏的字符串切片瞬间翻车。

SerialForge 提倡**集中声明**：把所有和协议相关的细节写在一处。每一个声明对象都是冻结且不可变的，库在全链路内部严格保持对象的身份同一性。这意味着在回调函数里，你可以底气十足地使用 `message.spec is READ_VOLTAGE` 来分流处理，不需要猜字符串。

---

## 声明的基本骨架

一条命令声明主要包含四部分：

```python
from serialforge import ResponseMode, Spec

# 1. 最简单的单发单收文本命令
PING = forge.add("PING", r"PONG")

# 2. 带占位符参数的控制命令
SET_SPEED = forge.add("SET_RPM {rpm}", r"OK")

# 3. 自动解析为结构化数据的采集命令
READ_ADC = forge.add("READ_ADC", r"ADC:\s*(?P<val>[\d\.]+)", result_type=float)

# 4. 只发不等的停机指令
STOP = forge.add("STOP", mode=ResponseMode.NO_REPLY)

# 5. 设备自发推送的报警事件（没有 request，只有 pattern）
ALARM = forge.add(pattern=r"ALARM:\s*(?P<code>\d+)", result_type=int)
```

关于细节的贴心设计：
- `request` 末尾的 `\r\n` 换行符由库统一处理，你不用手动敲；即便不小心写了，库也会规整好，不会发出去两份换行。
- `pattern` 正则表达式匹配的是**剥离了换行符的一整帧文本**。如果设备回包是 `PONG\r\n`，你的正则只需要写 `PONG`。

---

## 五种对话姿势（ResponseMode）

工业场景下的通信千奇百怪，SerialForge 将其归纳为五种清晰的模式：

| 模式 | 声明方式 | 形象比喻 | 典型应用场景 |
| --- | --- | --- | --- |
| **单发单收** | 默认 `SINGLE` | 你问一句，它答一句 | 读取版本号、查询当前状态 |
| **单发多收** | `mode=ResponseMode.MULTI` | 老师点名，学生挨个喊到 | `ReadAll` 批量拉取传感器列表 |
| **流式采集** | `mode=ResponseMode.STREAM` | 打开水龙头，水一直流 | 示波器波形采集、持续心率监测 |
| **无回复** | `mode=ResponseMode.NO_REPLY` | 喊一声“关灯”，不需要它吭声 | 复位指令、紧急停机、波特率切换 |
| **主动上报** | 仅提供 `pattern`，省略 `request` | 门铃响了，有人主动按门铃 | 设备自发的过热报警、外部按键中断 |

### 1. 单发多收（MULTI）的断句规则
对于多行响应，设备连吐多条数据，什么时候才算“回复完毕”？有两种明确的办法：

- **定数读取（`count=N`）**：明确知道设备会回固定行数。
  ```python
  READ_CHANNELS = forge.add(
      "READ_ALL",
      r"CH\d+:\s*(?P<val>[\d\.]+)",
      dict,
      mode=ResponseMode.MULTI,
      count=4,  # 读满 4 行自动完成
  )
  ```
- **哨兵标记读取（`until="END"`）**：行数不固定，以特定的结束标志行收尾。
  ```python
  DUMP_LOG = forge.add(
      "DUMP",
      r".+",
      mode=ResponseMode.MULTI,
      until="LOG_END",  # 读到 LOG_END 这一行自动收工
  )
  ```

如果设备吐了两行就因为某种原因卡壳闭嘴了，`idle_timeout_s`（默认 0.3 秒）行间超时会保护你，库会提前终止等待并产出终态，绝不把线程吊死在半空中。

### 2. 流式采集（STREAM）
流式命令发出去后，设备会源源不断地往外吐数据。这种命令**只能通过异步 `send_async` 发送**，调用时给的超时时间就是你的最大采集时长。想要中途关掉水龙头，详见后续的 [流式与主动上报](stream-events.md) 章节。

---

## 杀手级特性：自动数据解析（result_type）

还在手写字符串切分？来看看 SerialForge 的类型自动映射：

### 1. 标量自动转换：`int` 与 `float`
如果你的正则捕获组提取的是数值，只要写 `result_type=int` 或 `result_type=float`，库会自动找到第一个捕获组并调用类型转换：

```python
GET_TEMP = forge.add("GET_TEMP", r"TEMP:\s*(?P<temp>[\d\.]+)", result_type=float)

msg = forge.send_sync(GET_TEMP)
temp = msg.data[0]
print(temp + 1.5)  # 直接当 float 用，无需手动 float(raw_str)
```

### 2. 键值对字典：`dict`
当一行数据包含好几个字段时，用命名捕获组配上 `result_type=dict`：

```python
GET_STATUS = forge.add(
    "STATUS",
    r"STATE:(?P<state>\w+)\s+ERR:(?P<err>\d+)",
    result_type=dict,
)

msg = forge.send_sync(GET_STATUS)
# 得到字典: {'state': 'RUNNING', 'err': '0'}
print(msg.data[0]["state"])
```

### 3. 数据类强类型映射：`@dataclass`
这是工程维护的终极利器。定义一个标准的 Python `@dataclass`，字段名只要和正则里的命名捕获组一致，库会自动帮你完成实例化与字段类型转换（支持 `str`、`int`、`float` 以及 `Enum`）：

```python
from dataclasses import dataclass
from enum import Enum

class WorkMode(Enum):
    MANUAL = "MANUAL"
    AUTO = "AUTO"

@dataclass
class DeviceReport:
    rpm: int
    voltage: float
    mode: WorkMode

# 正则的命名捕获组名称对应 dataclass 属性名
QUERY_REPORT = forge.add(
    "REPORT",
    r"RPM:(?P<rpm>\d+)\s+V:(?P<voltage>[\d\.]+)\s+MODE:(?P<mode>\w+)",
    result_type=DeviceReport,
)

msg = forge.send_sync(QUERY_REPORT)
report: DeviceReport = msg.data[0]
print(f"转速: {report.rpm}, 电压: {report.voltage}V, 模式: {report.mode.name}")
```
这样写出来的业务代码有完整的 IDE 代码补全和类型检查，重构时安稳很多。

### 4. 自定义解析函数：`parser`
如果下位机丢出来的是十六进制编码、校验和或者自定义逻辑，你可以传一个可调用函数：

```python
def parse_hex_crc(raw: str) -> dict:
    payload = bytes.fromhex(raw)
    # 自定义解码与校验逻辑...
    return {"val": payload[0]}

CUSTOM_CMD = forge.add("GET_RAW", r"[0-9A-F]+", parser=parse_hex_crc)
```

---

## 统一的数据形态：`Message.data` 永远是列表

无论底层收到了什么，SerialForge 交付给你的 `Message.data` **永远是一个 `list`**：
- 单条回复（`SINGLE`）：单元素列表 `[result]`
- 多条回复（`MULTI`）：多元素列表 `[result1, result2, ...]`
- 无回复（`NO_REPLY`）：空列表 `[]`

这种一致性设计的最大好处是，你在处理返回数据时永远只需要关注 `msg.data`，不需要做繁琐的 `isinstance(data, list)` 分支判断。

下一步：[发送、回调与取消](sending.md)
