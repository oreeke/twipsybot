---
title: 开发环境
description: 配置 TwipsyBot 本地开发环境并运行源码、配置检查和测试。
---

# 开发环境

TwipsyBot 需要 Python 3.11 或更高版本，依赖和锁文件由 [uv](https://docs.astral.sh/uv/) 管理。

## 准备环境

```bash
git clone https://github.com/oreeke/twipsybot.git
cd twipsybot
conda create -n twipsybot-dev python=3.11
conda activate twipsybot-dev
uv sync --extra dev --locked
```

`--locked` 会在 `pyproject.toml` 与 `uv.lock` 不一致时失败，避免本地环境静默修改锁文件。

## 准备配置

```bash
cp config.yaml.example config.yaml
cp plugins/config.yaml.example plugins/config.yaml
uv run twipsybot config-check
```

填写测试账号与模型服务凭据后，可以从源码环境启动：

```bash
uv run twipsybot run
```

普通单元测试不需要真实 Misskey 或模型服务。涉及实际账号的手工测试应使用独立机器人账号。

## 常用命令

```bash
uv run pytest -q
uv run pyright
uvx pre-commit run --all-files
```

开发依赖声明在 `pyproject.toml` 的 `dev` extra 中。增加或升级依赖时使用 uv 更新锁文件，不要手工编辑 `uv.lock`。
