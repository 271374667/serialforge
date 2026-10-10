"""异步回调消费：按文档推荐的方式把结果交给主线程闭包（无硬件）。

``send_async()`` 之后用 ``add_done_callback()`` 注册一个只接收 ``Message``
的闭包，比手动泵事件更贴近真实 GUI：闭包统一排队到 Qt 应用主线程，可以
在里面直接更新界面。

三件事：

1. 成功：``send_async(spec, context=...).add_done_callback(consume)``。
   默认 3 秒期限从提交开始，包含排队、设备等待与主线程投递；成功后补注册的
   闭包也会排队回放，闭包 / ``received`` / ``result()`` 是同一个 ``Message``。
2. 失败：超时不执行成功闭包，``done`` 为真后用 ``result()`` 取失败——它会
   重抛 ``serialforge.errors`` 里的类型化异常。用来制造超时的“无人应答”声明
   直接写在本示例里（业务命令只属于使用方）。
3. ``STREAM`` 只能异步发送：帧从 ``received`` 逐帧出来，用 ``cancel()``
   给出 ``CANCELLED`` 终态。

虚拟设备与它的指令集见 ``examples/virtual_device.py``。
运行：``uv run python examples/async_callbacks.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 允许直接 ``python examples/async_callbacks.py`` 运行时找到项目根。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.virtual_device import MONITOR, VERSION, VirtualDevice
from serialforge import (
    DeviceProfile,
    Message,
    MessageCategory,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import SerialConfig
from serialforge.errors import CommandExecutionError
from serialforge.qt_core import QCoreApplication


class AsyncCallbackDemo:
    """演示推荐的主线程闭包消费方式。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、门面与信号收集容器。"""
        # SerialForge 构造时就会检查 Qt 应用是否存在，所以先建它。
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        # SerialForge 用 DeviceProfile 描述设备；probe 决定“怎么确认是它”。
        self._forge = SerialForge(self._build_profile())
        for declaration in self._device.declarations():
            self._forge.add(declaration)
        # 闭包收到的 Message、终态结果、流式帧分桶收集。
        self._callbacks: list[Message[Any]] = []
        self._results: list[Message[Any]] = []
        self._frames: list[Message[Any]] = []
        self._forge.received.connect(self._collect)

    def _build_profile(self) -> DeviceProfile:
        """设备描述：身份与波特率取自虚拟设备，probe 是版本查询。"""
        return DeviceProfile(
            vid_pid=list(self._device.vid_pid),
            baudrates=[self._device.baud_rate],
            probe=VERSION,
            # 真机请保留默认的 0.3 秒开串口稳定等待；示例设为 0 只为省时间。
            serial=SerialConfig(settle_time_s=0),
        )

    def _collect(self, message: Message[Any]) -> None:
        """按 category 分桶，方便下面顺序演示。"""
        if message.category is MessageCategory.COMMAND_RESULT:
            self._results.append(message)
        elif message.category is MessageCategory.STREAM_FRAME:
            self._frames.append(message)

    def run(self) -> int:
        """连上设备，依次演示成功回调、失败不回调与 STREAM 收尾。"""
        with self._device.attached():
            try:
                # 阻塞式连接：返回时设备已打开并初始化好，可以直接发送。
                self._forge.connect()
                print(f"[连接] {self._forge.state.value}")
                self._demo_done_callback()
                self._demo_failure_keeps_callback_silent()
                self._demo_stream_cancel()
            except SerialForgeError as error:
                print(f"[连接失败] {error}")
                return 1
            finally:
                # 退出前断开：它会等 I/O 线程停下来，避免解释器结束时线程悬挂。
                self._forge.disconnect()
        print("\n[完成] 三条异步路径都演示过了")
        return 0

    def _demo_done_callback(self) -> None:
        """1. 成功结果交给主线程闭包，并确认三处是同一个 Message。"""
        print("\n=== 1. add_done_callback 消费成功结果 ===")
        self._callbacks.clear()
        self._results.clear()
        # context 是每次调用独立的只读快照，闭包里可以按它回填界面。
        call = self._forge.send_async(VERSION, context={"row": 7})
        call.add_done_callback(self._on_message)
        self._wait_until(lambda: bool(self._callbacks))
        message = self._callbacks[0]
        print(f"  回调: {message.data}  context={message.context['row']}")
        print(f"  done={call.done}  callback_expired={call.callback_expired}")
        # 结果已经成功后再补注册：闭包同样排队回放，result() 不阻塞。
        call.add_done_callback(self._on_message)
        self._wait_until(
            lambda: (
                len(self._callbacks) >= 2
                and any(item.spec is VERSION for item in self._results)
            )
        )
        received_message = next(
            item for item in self._results if item.spec is VERSION
        )
        same = self._callbacks[1] is received_message is call.result()
        print(f"  闭包 / received / result() 同一对象: {same}")

    def _demo_failure_keeps_callback_silent(self) -> None:
        """2. 超时不执行成功闭包，失败要用 result() 取。"""
        print("\n=== 2. 超时不回调，失败从 result() 重抛 ===")
        self._callbacks.clear()
        # 声明写在使用方这一侧；虚拟设备不认识这条请求，永远等不到回复。
        silent = self._forge.add("NoAnswer", r"ANSWER \d+")
        # 0.4 秒是本地期限：闭包注册成功，但超时后不会被调用。
        call = self._forge.send_async(silent, timeout_s=0.4)
        call.add_done_callback(self._on_message)
        self._wait_until(lambda: call.done)
        print(f"  done={call.done}  成功闭包执行次数={len(self._callbacks)}")
        try:
            call.result()
        except CommandExecutionError as error:
            print(f"  result() 重抛 {type(error).__name__}")

    def _demo_stream_cancel(self) -> None:
        """3. STREAM 帧逐帧到达，用 cancel() 给出终态。"""
        print("\n=== 3. STREAM 帧从 received 来，cancel() 收尾 ===")
        self._frames.clear()
        self._results.clear()
        # 流的存活期限来自这一次调用：默认 3 秒会把它掐断。
        ticket = self._forge.send_async(MONITOR, timeout_s=60.0)
        self._wait_until(lambda: len(self._frames) >= 3)
        # 流式调用不会自己结束：cancel() 只影响本地调用与闭包，
        # 已经写出去的帧照旧会到达并进入 received。
        ticket.cancel()
        self._wait_until(
            lambda: any(item.spec is MONITOR for item in self._results)
        )
        final = next(item for item in self._results if item.spec is MONITOR)
        for frame in self._frames:
            print(f"  流式帧: {frame.data}")
        print(f"  流式命令终态: {final.status.value}")

    def _on_message(self, message: Message[Any]) -> None:
        """消费闭包：注册后统一在 Qt 应用主线程被调用。"""
        self._callbacks.append(message)

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float = 3.0,
    ) -> None:
        """泵 Qt 事件直到条件成立（真实应用由 ``app.exec()`` 负责这件事）。

        ``connect()`` 不需要它——连接自己阻塞到就绪。闭包、结果和流式帧都由
        I/O 工作线程发出、在主线程排队，必须转一次事件循环才会被执行。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待超时：异步回调或流式帧没有按预期到达")


if __name__ == "__main__":
    raise SystemExit(AsyncCallbackDemo().run())
