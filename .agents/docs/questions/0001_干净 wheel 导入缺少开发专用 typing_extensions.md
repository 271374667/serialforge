# 0001_干净 wheel 导入缺少开发专用 typing_extensions

> 分类: questions | 发现: 2026-10-08 | 状态: 已修复 | 严重级别: 高

## 现象

项目环境 pytest 全绿，但干净环境安装 wheel 后 `import serialforge` 抛出
`ModuleNotFoundError: No module named 'typing_extensions'`。

## 复现步骤

构建 wheel，用 `uv run --no-project --isolated --with <wheel> python -c
"import serialforge"` 验证。环境只能安装声明的运行依赖，不能依赖项目 `.venv`。

## 排查过程

- 在 M7 wheel 冒烟中定位到 ConnectionWorker、ReadThread、AsyncScanThread 的 override 导入。
- 包元数据仅声明 QtCore 绑定、pyserial、loguru；typing-extensions 只在 dev group。

## 根本原因

项目 `.venv` 同时包含运行和开发依赖，掩盖了生产源码对开发 backport 的引用。
构建/twine check 只检查产物结构和元数据，不能证明 import 成功。

## 修复方案

`84fa007` 移除生产类的 backport 导入及装饰器；Python 3.11 没有 stdlib override，
为三个 run 方法添加精确的 ty 说明。开发专用 Qt 类型测试仍可以使用 dev group 的
typing_extensions。安装包不再额外要求该运行依赖。

修复后干净 wheel 导入、Demo 和外置测试通过（97 通过、1 硬件跳过）。uv 的 --with
依赖层可能与 sys.prefix 分离，来源核对必须使用包元数据定位和 site-packages 路径。

## 如何避免再犯

- 发布前必须在源码之外安装 wheel，清除继承的 Python 路径，执行导入、Demo 和测试。
- 不能用项目环境的 import 或 pytest 代替安装包验证。
- 生产依赖按实际导入声明；测试替身只用于外置验收，不进入 wheel/sdist。
