---
title: 开发环境
description: 配置 TwipsyBot 本地开发环境并从源码运行。
---

# 开发环境

依赖与锁文件由 [uv](https://docs.astral.sh/uv/) 管理：

```bash
git clone https://github.com/oreeke/twipsybot.git && cd twipsybot
uv sync --python 3.11 --extra dev --locked
```

`--locked` 在 `pyproject.toml` 与 `uv.lock` 不一致时失败。依赖变更只通过 uv 命令完成。

## 运行

```bash
uv run --locked twipsybot cfg
uv run --locked twipsybot config-check
uv run --locked twipsybot run
```

手工联调使用独立测试账号；自动化测试不依赖真实服务，见[测试](testing.md)。
