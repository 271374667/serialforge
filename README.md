# serialforge

> 适用版本: 0.0.1  |  当前里程碑: M1

`serialforge` 是面向 Windows 10/11 64 位应用的 PySide6 串口通讯库。设计目标是用 QtCore 信号槽和线程封装设备发现、命令调度、自动重连、流量日志和无硬件测试后端。

当前版本完成包骨架与定义层，真实串口传输和调度将在后续里程碑实现。设计契约见 `串口通讯模块_方案_v10定稿.md`，AI 协作入口见 `AGENTS.md`。

## 开发

```powershell
uv sync
uv run python scripts/ci.py --fast
uv run python scripts/ci.py
```

项目仅支持 Windows 10/11 64 位；当前未执行 PyPI 上传或真实硬件验证。
