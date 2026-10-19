"""最简单的用法：同步扫描、连接、发一条命令，然后断开（无硬件）。

这是“先跑通”的版本，不涉及回调、线程切换或界面。脚本和后台工作线程都可以
直接照这个顺序写；GUI 主线程请换成 ``async_callback.py`` 或进阶示例里的异步版。

运行：``uv run python examples/simple/first_command.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.qt_app import QCoreApplication
from examples.virtual_device import VERSION, VirtualDevice
from serialforge import SerialForge
from serialforge.advanced import SerialConfig


def main() -> int:
    """扫描并连接虚拟设备，读一次版本号。"""
    _application = QCoreApplication.instance() or QCoreApplication([])
    device = VirtualDevice()

    with device.attached():
        forge = SerialForge()
        version = forge.add(VERSION)
        devices = forge.scan_sync(
            baudrates=[device.baud_rate],
            probe=version,
            vid_pid=device.vid_pid,
            # 真机保留默认的 0.3 秒稳定等待；示例设为 0 只为省时间。
            serial=SerialConfig(settle_time_s=0),
        )
        if not devices:
            print("没有找到设备")
            return 1

        forge.connect_sync(devices[0])
        message = forge.send_sync(version)
        print(f"已连接 {devices[0].port}，版本 {message.data[0].version}")
        forge.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
