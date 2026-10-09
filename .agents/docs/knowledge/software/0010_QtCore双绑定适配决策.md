# 0010_QtCore双绑定适配决策

> 分类: knowledge/software | 创建: 2026-10-09 | 适用范围: R5 Qt6 适配与打包
> 状态: R1 已批准的目标契约；R2–R5 未实现，当前运行 API 仍为旧基线

## 决定

采用内部窄 Qt6 适配模块 `serialforge.qt_core`，只直接导入 PySide6.QtCore 或 PyQt6.QtCore，不引入 QtPy。首期支持目标为 PySide6/PyQt6；Qt5、无 Qt 后端和低于 Python 3.11 不在本轮范围。适配层尚未实现，双绑定兼容尚未验证。

## 核实依据

固定发行版 QtPy 2.4.3 的 [QtCore.py 源码](https://github.com/spyder-ide/qtpy/blob/v2.4.3/qtpy/QtCore.py) 在 PyQt6 分支尝试导入 QtGui，在 PySide6 缺少 mightBeRichText 时导入 QtGui；[初始化源码](https://github.com/spyder-ide/qtpy/blob/v2.4.3/qtpy/__init__.py) 还写入 QT_API、尝试 QtDataVisualization。这些行为不适合作为本库严格 QtCore-only 边界。

这是源码路径审计，没有安装或运行 QtPy。此前用户批准方案已明确：若 QtPy 引入禁止模块，改用窄 Qt6 层，无需放宽硬约束。

## 选择算法

1. 首次 import 读取 QT_API，只接受 pyside6/pyqt6，不修改宿主环境变量。
2. 检查进程已经加载的 Qt 绑定；已加载一个支持的 Qt6 绑定则复用，与显式 QT_API 不一致时报 ConfigError。
3. 已加载两个绑定或已加载 Qt5 时拒绝，不静默混用或迁移绑定。
4. 无已加载绑定且 QT_API 明确时严格选定，不因缺少目标绑定回退。
5. 无明确选择：只安装一种受支持绑定则使用它；两者皆安装则提示必须设置 QT_API；皆无则 ImportError 说明对应 extra。
6. 检测包可用性不得导入 QtGui/QtWidgets；不安装软件、不创建应用/线程/日志/文件/串口。选定绑定后不热切换。

## 适配范围与架构守卫

- 别名 Signal/Slot/Property 对应 PyQt6 的 pyqtSignal/pyqtSlot/pyqtProperty；直接再导出所需 QObject/QThread/QCoreApplication/QTimer/QEventLoop/Qt。
- 仅覆写 Python 名称出口，不 monkey-patch Qt 类型；使用 Qt6 scoped 枚举。
- qt_core 属于全局基础设施边界，仅依赖标准库、errors、选定 QtCore。定义层数据模型仍不得导入实现子包。
- 适配层是函数/别名模块，架构守卫豁免“一文件一个同名类”；其他模块统一绝对导入 serialforge.qt_core。
- Signal(object) 跨线程身份、旧 connect/disconnect 名称冲突、Slot 执行线程、QObject 生命周期分别验收，不从 PySide6 结果推断 PyQt6 已通过。

## 打包与依赖边界

R1 不修改 pyproject.toml/uv.lock，不安装新依赖。R5 将 Qt 绑定移动到可选 extras：pyside6→PySide6-Essentials，pyqt6→PyQt6；核心仍为 pyserial/loguru。开发默认选择 pyside6。

当前 PySide6 下限仍为已验证的 >=6.11.2。PyQt6 的 Windows x64 wheel/Python 范围、下限与分发条件在 R5 实测后确定，不能在 R1 写成已验证兼容性；不得引入 QtPy。

每种绑定在独立进程/环境运行有效 Python 3.11–3.14 组合并构建干净 wheel。仅导入 QtCore，不承诺 Qt DLL 的内部本机依赖也完全没有其他库；禁止边界指 Python QtGui/QtWidgets 模块导入。
