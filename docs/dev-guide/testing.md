---
title: 测试
description: 运行 TwipsyBot 的 pytest、Pyright、锁文件与 pre-commit 检查。
---

# 测试

```bash
uv run --locked pytest -q
uv run --locked pytest tests/core -q
uv run --locked pytest tests/plugins -q
uv run --locked pytest -k auto_post -q
```

`tests/core/` 覆盖核心与插件框架，`tests/plugins/` 按插件拆分。

`tests/conftest.py` 提供配置工厂、临时插件目录、机器人构造器与模拟 Misskey 服务，网络行为一律通过模拟服务验证。

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
- pre-commit 检查锁文件、自动修复 Ruff lint 与格式，并执行 `pyupgrade --py311-plus`。

## 提交前

```bash
uvx pre-commit run --all-files
uv run --locked pytest -q
uv run --locked pyright
```

CI 在 push 与 PR 上运行测试、锁文件检查与 pre-commit，但不能替代本地针对性测试。
