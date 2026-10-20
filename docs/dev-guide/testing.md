---
title: 测试
description: 运行 TwipsyBot 的 pytest、Pyright、锁文件与 pre-commit 检查。
---

# 测试

```bash
uv run --locked pytest -q
uv run --locked pytest tests/bot -q
uv run --locked pytest tests/plugins/test_bond.py -q
uv run --locked pytest -k auto_post -q
uv run --locked pytest --cov
```

## 目录

`tests/` 的子目录与 `twipsybot/` 的包一一对应，文件按行为主题命名，不强求与源文件一一对应。

```text
tests/
├── conftest.py     全局 fixture：模拟服务、配置工厂、临时插件目录、机器人构造器
├── support/        非 fixture 的辅助代码，显式导入
├── app/            启动重试、心跳与退出码
├── bot/            提及、聊天、命令、限流、发帖、管理命令、热重载与生命周期
├── clients/        Misskey REST/Streaming、OpenAI 与受限抓取
├── db/             SQLite 状态存储
├── plugin/         插件加载、事件、分发、生命周期与受限服务
├── shared/         配置、设置 IO 与运行状态
├── tui/            配置界面与日志页
└── plugins/        内置插件，每个插件一个文件
```

约定：

- 通过 `make_bot` 与模拟服务做黑盒验证，网络行为不使用真实请求。
- 共享常量与工厂放进 `support/`，仅某个目录使用的 fixture 放进该目录的 `conftest.py`，用例文件内只保留自己用到的辅助。
- 用例名写成「行为 + 条件」，不写实现细节。
- 文件超过约 600 行时按主题再拆。
- 覆盖率统计 `twipsybot` 与 `plugins`，含分支，低于 `fail_under` 时 `--cov` 失败。
- pytest 以 `--import-mode=importlib` 与 `pythonpath = ["tests"]` 运行，不同目录可以出现同名测试文件，也不需要 `__init__.py`。

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
