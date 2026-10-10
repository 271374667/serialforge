# 0010_QtCore多绑定适配决策

> 分类: knowledge/software | 创建: 2026-10-09 | 更新: 2026-10-10 | 适用范围: R5 Qt 适配与打包
> 状态: Qt6 双绑定已通过 R5 验收；2026-10-10 用户要求后扩展为 PySide6/PyQt6/PyQt5 三绑定
> 备注: 文件名沿用创建时的“双绑定”字样以保持既有引用稳定，内容已扩展为三绑定。

## 决定

采用内部窄 QtCore 适配模块 `serialforge.qt_core`，只直接导入 `PySide6.QtCore`、
`PyQt6.QtCore` 或 `PyQt5.QtCore`，不引入 QtPy。首期支持 PySide6/PyQt6；2026-10-10
用户明确要求“正式支持 PyQt5”后扩展为三绑定。无 Qt 后端、`PySide2` 与低于
Python 3.11 仍不在范围。

## 核实依据

固定发行版 QtPy 2.4.3 的 [QtCore.py 源码](https://github.com/spyder-ide/qtpy/blob/v2.4.3/qtpy/QtCore.py) 在 PyQt6 分支尝试导入 QtGui，在 PySide6 缺少 mightBeRichText 时导入 QtGui；[初始化源码](https://github.com/spyder-ide/qtpy/blob/v2.4.3/qtpy/__init__.py) 还写入 QT_API、尝试 QtDataVisualization。这些行为不适合作为本库严格 QtCore-only 边界。

这是源码路径审计，没有安装或运行 QtPy。此前用户批准方案已明确：若 QtPy 引入禁止模块，改用窄 QtCore 层，无需放宽硬约束；本库自研的 qt_core 已等价覆盖该职责。

## 选择算法

1. 首次 import 读取 QT_API，接受 `pyside6`/`pyqt6`/`pyqt5`，不修改宿主环境变量。
2. 检查进程已经加载的 Qt 绑定；已加载一个受支持绑定则复用，与显式 QT_API 不一致时报 ImportError。
3. 已加载两个及以上受支持绑定时报错；已加载 `PySide2` 时明确报“不支持 PySide2”。
4. 无已加载绑定且 QT_API 明确时严格选定，不因缺少目标绑定回退。
5. 无明确选择：只安装一种受支持绑定则使用它；多个已安装则提示必须设置 QT_API；皆无则 ImportError 说明对应 extra。
6. 检测包可用性不得导入 QtGui/QtWidgets；不安装软件、不创建应用/线程/日志/文件/串口。选定绑定后不热切换。

## 适配范围与架构守卫

- 别名 Signal/Slot/Property 对应 PyQt6/PyQt5 的 pyqtSignal/pyqtSlot/pyqtProperty；直接再导出 QObject/QThread/QCoreApplication/QTimer/QStandardPaths/Qt。
- 仅覆写 Python 名称出口，不 monkey-patch Qt 类型；使用 Qt scoped 枚举。实测本库用到的 `Qt.ConnectionType.*` 与 `QStandardPaths.StandardLocation.*` 在 PyQt5 5.15.11 上同样可用，源码无需按 Qt 版本分叉枚举写法。
- qt_core 属于全局基础设施边界，仅依赖标准库、errors、选定 QtCore。定义层数据模型仍不得导入实现子包。
- 适配层是函数/别名模块，架构守卫豁免“一文件一个同名类”；其他模块统一绝对导入 serialforge.qt_core。
- Signal(object) 跨线程身份、旧 connect/disconnect 名称冲突、Slot 执行线程、QObject 生命周期分别验收，不从 PySide6 结果推断 PyQt6/PyQt5 已通过。

## 打包与依赖边界

extras：`pyside6`→PySide6-Essentials（下限实测 `>=6.11.2`）、`pyqt6`→PyQt6（`>=6.11`）、
`pyqt5`→PyQt5（`>=5.15.11`）；核心仍为 pyserial/loguru。开发默认选择 pyside6。

- 许可：PySide6 为 LGPL；PyQt6/PyQt5 为 Riverbank GPL v3 / 商业双授权，作为可选 extra 由使用方自行履行许可义务。
- 平台 wheel：`pyqt5-qt5` 的新版本（如 5.15.19）没有 win_amd64 wheel，因此 `pyproject.toml`
  用 `[tool.uv] required-environments` 声明 Windows x64，锁文件对 `pyqt5-qt5` 生成平台标记
  （win_amd64→5.15.2，其他平台→5.15.19）。这与 `rules/09` 只面向 Windows x64 一致。
- 不得引入 QtPy；每种绑定在独立环境/进程运行并构建干净 wheel。

## 已验证范围（2026-10-10）

- 三个绑定在 Python 3.11 各跑完整测试套件：`pyside6`/`pyqt6`/`pyqt5` 均 175 项、
  0 失败、0 错误；跳过项为未安装绑定与硬件用例。
- PyQt5 干净 wheel 冒烟（`scripts/ci.py --smoke --binding pyqt5`）：171 通过、4 跳过。
- PyQt5 在 Python 3.11 与 3.14 均可安装并导入 QtCore（abi3 wheel）。

未验证：Python 3.12/3.13 的 PyQt5 组合、PyQt5 下的真机/硬件用例；完整三绑定 × 四版本矩阵
（12 组合）本次未全跑，CI 已扩展为覆盖该矩阵。
