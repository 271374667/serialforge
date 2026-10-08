# serialforge 发布流程

> 适用版本: 0.0.1

发布只能由维护者在本地 Windows 10/11 x64 环境执行。Codex 开发流程不执行上传、推送
或 tag；`scripts/release.py` 默认只做 dry-run。

## 发布前检查

```powershell
uv sync --locked
uv run python scripts/ci.py --release
```

这一步覆盖单版本全量测试、Ruff、ty、pylint、构建、`twine check`、wheel 干净环境冒烟、
Python 3.11–3.14 矩阵和最低直接依赖检查。`--release` 使用独立的 `UV_PROJECT_ENVIRONMENT`
目录，不应修改已提交的 `uv.lock`。

确认工作区干净后再修改版本：

```powershell
uv version --bump patch
uv sync
uv run python scripts/ci.py --release
```

## 构建与检查

构建物统一写入 `outputs/01_dist/`：

```powershell
uv build --out-dir outputs/01_dist --clear
uv run twine check outputs/01_dist/serialforge-0.0.1-py3-none-any.whl outputs/01_dist/serialforge-0.0.1.tar.gz
```

不要把 `dist/`、`build/`、wheel、sdist 或日志提交到 Git。构建后可把 wheel 拷贝到项目
目录之外，用 `uv run --isolated` 或另一台机器安装并运行：

```powershell
uv run python scripts/ci.py --smoke --wheel outputs/01_dist/serialforge-0.0.1-py3-none-any.whl
```

## 发布脚本

```powershell
uv run python scripts/release.py
```

提交经过检查的版本、文档和锁文件变更，确保工作区干净后运行脚本。默认模式运行完整
发布前 CI，检查工作区及构建物，不访问上传接口；如工作区有改动会直接失败，本次用户
既有的 `main.py` 删除也不会被脚本自动提交。

真正发布必须由维护者在交互终端显式执行 `--publish --target testpypi`，输入
`publish testpypi`；在 TestPyPI 安装验收后，再人工执行 `--publish --target pypi`，输入
`publish pypi`。默认目标是 TestPyPI。传输令牌由 uv 从 `UV_PUBLISH_TOKEN` 或系统 keyring
读取，不写入仓库、参数或日志；TestPyPI 和 PyPI 使用各自的账户与令牌。

TestPyPI 安装验证应在仓库外执行 `uv run --no-project --isolated --with
"serialforge==<版本>" --default-index https://test.pypi.org/simple --index
https://pypi.org/simple python -c "import serialforge"`，确认拿到 TestPyPI 上的目标版本。
然后用本仓库的外置 Demo 和测试做验收。已用过的 PyPI 版本不能重传；首次上传后改用
项目范围令牌。使用方依赖从 PyPI 获取，库不采用 Trusted Publishing 或云端 CI。

本项目规则禁止 AI 执行 `scripts/release.py --publish`、`uv publish`、`git push` 或
创建 tag。
