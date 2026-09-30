# 06 · Python 工具链

管理 Python 环境、依赖、检查或测试时读本文件。

## 1. 版本和项目形态

- 新项目首选 uv、pytest、Ruff、ty；存量项目保留现有工具，迁移作为独立任务评估。
- Python 3.11 是新项目参考基线，不强制降级已有项目。依据依赖支持范围选择解释器。
- `requires-python` 表示最低兼容范围，`.python-version` 固定开发解释器，
  Ruff target-version 和 ty python-version 应与项目支持范围匹配。
- 标准布局为 `src/<package>/`，从实际包名导入，不能将 src 当作包名。
- 可安装库或应用配置 build-system 和包发现；单文件脚本不强制引入构建后端。
- 开发依赖只放 dependency-groups.dev；只有用户可选功能才放 optional-dependencies。

## 2. PowerShell 命令

下面命令兼容 PowerShell 5.1 和 7，逐条执行并检查 $LASTEXITCODE。
路径和含环境标记的参数必须加引号，不用 Bash 续行、sed、/dev/null。

```powershell
uv python pin 3.11              # 示例，替换为项目选择的版本
uv sync                        # 日常开发，可能更新锁文件
uv sync --locked               # CI / 复现，锁文件过期时失败
uv add --dev pytest ruff ty     # 已确定使用这些开发工具时
uv run python -m my_package
uv run pytest tests/test_loader.py
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

提交 uv.lock，不提交 .venv。升级检查工具是可审查的依赖更新，允许固定兼容版本或回退有回归的版本。

## 3. 新项目配置参考

这是可安装包的参考，项目名、版本范围和包布局需按实际填写。

```toml
[project]
name = "my-package"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = ["pytest", "ruff", "ty"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/my_package"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"

[tool.ruff]
target-version = "py311"
line-length = 100
extend-exclude = ["temp", "outputs", "dependence", "bin", "config", "data"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = []

[tool.ty.environment]
python-version = "3.11"

[tool.ty.src]
exclude = [".venv", "temp", "outputs", "dependence", "bin", "config", "data"]
```

其他 Ruff 规则组按项目风险逐步采用。CLI 正式输出允许 print，不统一启用 T20 禁止全部输出。
ty 严格规则可按所用版本验证后启用；不把未核实的规则名和默认级别当作固定契约。
存量类型检查器的插件、平台设置和豁免可能不能等价迁移，须记录差异并验证。
保留已有项目 ignore 的理由，新增豁免注明范围和原因，避免裸 ignore。

## 4. 检查与测试

- 接入规范先运行只读检查，记录已有失败；不以“接入”为由修复全部历史问题。
- **ruff format 与 ty check 只在完成一个大任务时跑一次**（收尾时刻，不是中间阶段）；
  中间提交、checkpoint 提交一律不跑——小步快跑，提交速度优先（见 `rules/05` §11）。
  收尾跑完若报错误，当场修复后再做收尾提交。
- 仅对本次任务修改的明确文件运行 Ruff，保护用户已有改动。
- 收尾修复顺序：ruff check --fix → ruff format → ty check。
  每条命令检查退出码；修复后审阅 diff 并运行相关测试。
- 单模块改动跑定向测试；公共接口变动覆盖下游；大型重构、阶段完成或发布前跑全量。
  小项目全量测试很快时可直接全量，不机械禁止。
- bug 修复补能复现行为的回归测试，文件 IO 用 tmp_path，外部依赖隔离。
- 打包或导入布局变更后，在干净环境安装 wheel，从源码根目录之外验证导入与入口；
  仅在项目根运行成功不能证明安装包正确。
- 工具不可用、离线或检查失败时明确报告，不宣称验证通过。

## 5. Windows 10 x64

- 检查解释器位数与版本：`uv run python -c "import struct, sys; print(sys.version); print(struct.calcsize('P') * 8)"`。
- 兼容 wheel 包括纯 Python 的 py3-none-any，以及匹配解释器 ABI 的 win_amd64 wheel。
  没有 wheel 时评估编译工具和依赖成本，不能仅凭包名判断。
- 路径使用 pathlib；测试包含中文、空格、大小写冲突、文件占用和必要的长路径场景。
- 文件显式 UTF-8；读取 Windows 工具生成的 JSON 可用 utf-8-sig 接受 BOM。
- 子进程用参数列表，明确 cwd、超时和所需编码，不依赖当前终端的默认编码。
- 多进程入口使用 __main__ 保护；冻结程序在入口调用 multiprocessing.freeze_support()。
- DLL 用明确的资源定位与加载目录；不要假定开发目录、PATH 或当前工作目录在打包后不变。
