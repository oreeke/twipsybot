---
title: 测试
description: 运行 TwipsyBot 的 pytest、Pyright、锁文件与 pre-commit 检查。
---

# 测试

```bash
uv run --locked pytest -q
uv run --locked pytest tests/framework/bot -q
uv run --locked pytest tests/builtin/test_bond.py -q
uv run --locked pytest -k auto_post -q
uv run --locked pytest --cov
```

## 目录

文件按行为主题命名，不强求与源文件一一对应。

```text
tests/
├── conftest.py     全局 fixture
├── support/        共享工具：模拟服务、数据构造、类型
├── framework/      宿主框架
└── builtin/        内置插件
```

约定：

- `tests/` 在 `pythonpath` 上，其下的目录名即顶层导入名，因此顶层目录不得与 `twipsybot`、`plugins` 等真实包同名，这也是内置插件目录叫 `builtin` 的原因。
- `support/` 是唯一带 `__init__.py` 的目录，其余测试目录不需要也不要添加。
- 通过 `make_bot` 与模拟服务做黑盒验证，网络行为不使用真实请求。
- 共享常量与工厂放进 `support/`，仅某个目录使用的 fixture 放进该目录的 `conftest.py`，用例文件内只保留自己用到的辅助。
- 用例名写成「行为 + 条件」，不写实现细节。
- 文件超过约 600 行时按主题再拆。
- 覆盖率统计 `twipsybot` 与 `plugins`，含分支，低于 `fail_under` 时 `--cov` 失败。
- pytest 以 `--import-mode=importlib` 与 `pythonpath = ["tests"]` 运行，不同目录可以出现同名测试文件。

插件测试至少覆盖：

- 配置默认值、边界值与无效值
- Hook 的未处理、正常与失败路径
- 返回结构与优先级行为
- 存储仅在操作成功后更新
- 初始化、关闭与取消时的资源释放

## 静态检查

```bash
uv run --locked pyright
uv lock --check
uvx pre-commit install
uvx pre-commit run --all-files
```

- Pyright 以 basic 模式检查 `twipsybot`、`plugins` 与 `tests`。
- pre-commit 检查锁文件、自动修复 Ruff lint 与格式。

## 提交前

```bash
uvx pre-commit run --all-files
uv run --locked pytest -q
uv run --locked pyright
```

CI 在 push 与 PR 上运行测试、锁文件检查与 pre-commit，但不能替代本地针对性测试。
