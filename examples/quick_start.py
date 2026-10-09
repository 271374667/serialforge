"""快速启动：一个文件看完全部“从设备拿信息”的方式（无硬件）。

本示例只写 serialforge 的事：建 Qt 应用、拼 ``DeviceProfile``、注册声明、
连接、发指令、收信号。虚拟设备与它的指令集（Version / ReadAll / Monitor /
Stop / Reboot / Ping / ALARM）见 ``examples/virtual_device.py``；真机上换成
``handler.connect("COM11")`` 即可。

覆盖的五种取信息方式：

1. 单发单收（SINGLE）：一条请求 → 一条回复
2. 单发多收（MULTI）：一条请求 → 多条回复（用 ``count`` 或 ``until`` 结束）
3. 流式输出（STREAM）：设备持续吐帧，逐帧从 ``received`` 出来
4. 主动上报（Spec）：设备没被问也会推数据
5. 无回复（NO_REPLY）：写完就结束，不等回复

``connect()`` 是阻塞的：返回时设备已经就绪，可以直接 ``send()``。示例里手动泵
事件只是为了收结果和事件信号——它们由 I/O 工作线程发出，槽在主线程，需要转
一次事件循环才会被调用。

运行：``uv run python examples/quick_start.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 允许直接 ``python examples/quick_start.py`` 运行（不经过包导入）时找到项目根。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.virtual_device import (
    ALARM,
    MONITOR,
    READ_ALL,
    REBOOT,
    STOP,
    VERSION,
    VirtualDevice,
)
from serialforge import (
    CommandSpec,
    DeviceProfile,
    Message,
    MessageCategory,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import CommandPriority, SerialConfig
from serialforge.qt_core import QCoreApplication


class QuickStartDemo:
    """按顺序演示五种取信息方式，全部逻辑都收在这个类里。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、handler 与信号收集容器。"""
        # SerialForge 构造时就会检查 Qt 应用是否存在，所以先建它。
        self._application = QCoreApplication.instance() or QCoreApplication([])
        # 虚拟设备的指令集见 examples/virtual_device.py 顶部的表格。
        self._device = VirtualDevice()
        self._handler = SerialForge(self._build_profile())
        self._results: list[Message[Any]] = []
        self._events: list[Message[Any]] = []
        # 命令必须先注册再发送，而且要用同一份声明对象（库不复制声明）。
        for declaration in self._device.declarations():
            self._handler.add(declaration)
        # 结果和事件都只从信号回来，这里先收进列表，方便下面顺序演示。
        self._handler.received.connect(
            lambda message: (
                self._results.append(message)
                if message.category is MessageCategory.COMMAND_RESULT
                else None
            )
        )
        self._handler.received.connect(
            lambda message: (
                self._events.append(message)
                if message.category
                in (
                    MessageCategory.DEVICE_EVENT,
                    MessageCategory.STREAM_FRAME,
                    MessageCategory.UNKNOWN_FRAME,
                )
                else None
            )
        )

    def _build_profile(self) -> DeviceProfile:
        """设备描述：身份与波特率取自虚拟设备，probe 决定“怎么确认是它”。"""
        return DeviceProfile(
            vid_pid=list(self._device.vid_pid),
            baudrates=[self._device.baud_rate],
            # 探测命令必须是 SINGLE 命令，而且 pattern 不能匹配自己的请求文本。
            probe=VERSION,
            # 真机请保留默认的 0.3 秒开串口稳定等待；示例把它设为 0 只为省时间。
            serial=SerialConfig(settle_time_s=0),
        )

    def run(self) -> int:
        """连上设备，依次演示五种取信息方式，返回进程退出码。"""
        with self._device.use():
            try:
                # 阻塞式连接：返回时设备已经打开并初始化好，可以直接发送。
                # 失败（没找到设备、端口被占用等）会同步抛 SerialForgeError。
                self._handler.connect()
                print(f"[连接] {self._handler.state.value}")
                self._demo_single()
                self._demo_multi()
                self._demo_stream()
                self._demo_event()
                self._demo_no_reply()
            except SerialForgeError as error:
                print(f"[连接失败] {error}")
                return 1
            finally:
                # 退出前断开：它会等 I/O 线程停下来，避免解释器结束时线程悬挂。
                self._handler.disconnect()
        print("\n[完成] 五种取信息方式都跑过了")
        return 0

    def _demo_single(self) -> None:
        """1. 单发单收：一条请求对应一条回复。"""
        print("\n=== 1. 单发单收（SINGLE）===")
        self._results.clear()
        self._handler.send_async(VERSION)
        result = self._wait_result(VERSION)
        print(f"  请求文本: {VERSION.request!r}")
        print(f"  解析结果: {result.data}  原始帧: {result.raw_frames[0]!r}")

    def _demo_multi(self) -> None:
        """2. 单发多收：MULTI 把多帧收进一个结果里。"""
        print("\n=== 2. 单发多收（MULTI，count=3）===")
        self._results.clear()
        self._handler.send_async(READ_ALL)
        result = self._wait_result(READ_ALL)
        # 没指定 result_type 时，每帧解析成一个 dict，整体是 list。
        assert isinstance(result.data, list)
        for index, frame in enumerate(result.data, start=1):
            print(f"  第 {index} 帧: {frame}")
        print(f"  帧数: {len(result.raw_frames)}")

    def _demo_stream(self) -> None:
        """3. 流式输出：STREAM 的帧只走 received。"""
        print("\n=== 3. 流式输出（STREAM）===")
        self._events.clear()
        ticket = self._handler.send_async(MONITOR)
        self._wait_until(lambda: len(self._frames_of(MONITOR)) >= 3)
        # 停止命令要插到普通命令前面，所以用 URGENT 优先级。
        self._handler.send_async(STOP, priority=CommandPriority.URGENT)
        self._wait_result(STOP)
        # cancel() 让流式命令自身也有明确的终态（CANCELLED），否则它会一直挂着。
        ticket.cancel()
        final = self._wait_result(MONITOR)
        for event in self._frames_of(MONITOR):
            print(f"  帧: {event.data}")
        print(f"  流式命令终态: {final.status.value}")

    def _demo_event(self) -> None:
        """4. 主动上报：设备没被问也会推数据。"""
        print("\n=== 4. 主动上报（Spec）===")
        self._events.clear()
        # 真机由设备自己推；这里让虚拟设备注入一帧 ALARM 42。
        self._device.inject_alarm()
        self._wait_until(lambda: bool(self._frames_of(ALARM)))
        event = self._frames_of(ALARM)[0]
        print(f"  事件: {event.data}  原始帧: {event.raw_frame!r}")
        print("  提示：没注册的帧也会进 received，此时 spec 为 None")

    def _demo_no_reply(self) -> None:
        """5. 无回复：写完立即结束，不等设备回答。"""
        print("\n=== 5. 无回复（NO_REPLY）===")
        self._results.clear()
        self._handler.send_async(REBOOT)
        result = self._wait_result(REBOOT)
        print(f"  终态: {result.status.value}  数据: {result.data}")
        print(f"  已写出字节: {result.sent!r}")

    def _frames_of(self, spec: object) -> list[Message[Any]]:
        """按对象身份筛事件：库不复制声明，所以 ``is`` 才可靠。"""
        return [event for event in self._events if event.spec is spec]

    def _wait_result(self, spec: CommandSpec) -> Message[Any]:
        """等到某个声明的终态结果并返回它。"""
        self._wait_until(
            lambda: any(item.spec is spec for item in self._results)
        )
        return next(item for item in self._results if item.spec is spec)

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float = 3.0,
    ) -> None:
        """泵 Qt 事件直到条件成立（只用于等命令结果与事件）。

        连接不需要它：``connect()`` 自己阻塞到设备就绪。但结果/事件由 I/O
        工作线程发出、槽在主线程，必须转一次事件循环才会被调用；真实应用由
        ``app.exec()`` 负责这件事，示例里手动 ``processEvents()`` 是为了让
        “发一条、收一条”的顺序一眼可见。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待超时：设备没有按预期返回数据")


if __name__ == "__main__":
    raise SystemExit(QuickStartDemo().run())
