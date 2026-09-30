# Git Hooks

本项目只启用 `.githooks/commit-msg`，通过以下配置生效：

```powershell
git config core.hooksPath .githooks
```

钩子只检查第一行提交标题是否符合 `type(scope): description`，正文、密钥、文件大小、测试、ruff 和 ty 均不扫描。环境异常时应失败安全；临时跳过可设置 `PAI_SKIP_HOOKS=1`，但需要在交付说明中记录。
