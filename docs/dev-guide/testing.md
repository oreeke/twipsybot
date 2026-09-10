---
title: 测试
description: 运行 TwipsyBot 的 pytest、Ruff、Pyright、锁文件和 pre-commit 检查。
---

# 测试

提交前应在 Python 3.11+ 环境安装开发依赖：

```bash
uv sync --extra dev --locked
```

## 测试

运行完整测试：

```bash
uv run --locked pytest -q
```

按文件或测试名称运行窄范围测试：

```bash
uv run --locked pytest tests/test_plugin.py -q
uv run --locked pytest -k auto_post -q
```

`tests/conftest.py` 提供配置工厂、临时插件目录、机器人构造器和模拟 Misskey 服务。网络行为应通过模拟服务验证，不要让自动化测试依赖真实实例或模型 API。

插件测试至少覆盖：

- 配置默认值、边界值和无效值。
- 对应事件 Hook 的不处理、正常处理和失败路径。
- 返回结果的结构与优先级行为。
- 存储状态只在成功操作后更新。
- 初始化、关闭和取消时的资源释放。

## 类型与锁文件检查

```bash
uv run --locked pyright
uv lock --check
```

Pyright 使用 basic 模式，检查 `twipsybot`、`plugins` 和 `tests`。

## pre-commit

```bash
uvx pre-commit install
uvx pre-commit run --all-files
```

钩子会检查锁文件，自动修复 Ruff lint 和格式问题，并使用 `pyupgrade --py311-plus` 更新语法。

## 检查单

```bash
uvx pre-commit run --all-files
uv run --locked pytest -q
uv run --locked pyright
uv lock --check
```

GitHub Actions 会在 push 和 pull request 上分别执行测试、锁文件检查和 pre-commit。不要依赖 CI 代替本地的行为范围测试。
