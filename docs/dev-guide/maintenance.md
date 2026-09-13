---
title: 维护
description: 维护 TwipsyBot 依赖、版本、变更记录、Docker 镜像和插件兼容性。
---

# 维护

项目版本以 `pyproject.toml` 的 `project.version` 为准。

面向用户的行为、配置、命令或兼容性发生变化时，应更新对应文档。

## 依赖变更

使用 uv 修改依赖并更新锁文件：

```bash
uv add <package>
uv add --optional dev <package>
uv lock --check
```

项目运行依赖当前使用精确版本。升级后应执行完整测试、Ruff、Pyright 和 pre-commit，并检查 Docker 构建是否仍能使用二进制 wheel 与哈希锁定安装。

## 版本发布

1. 更新 `pyproject.toml` 中的版本。
2. 更新受影响文档。
3. 同步 `uv.lock` 并完成全部质量检查。
4. 合并到 `main`。

`main` 分支上的版本变化会触发自动标签工作流，创建 `v<version>` 标签。不要手工创建不同格式的发布标签，也不要重复使用已有版本。

## Docker 镜像

`vX.Y.Z` 标签触发多架构镜像发布，目标为 `linux/amd64` 和 `linux/arm64`。发布标签包括完整版本、主次版本和 `latest`。

本地构建验证：

```bash
docker build -t twipsybot:dev .
docker run --rm twipsybot:dev --help
```

Dockerfile 使用多阶段构建，在 builder 中生成 wheel，运行阶段以非 root 用户启动。新增运行时文件时，需要确认它被复制到最终镜像且 `appuser` 具有所需权限。

## 兼容性

插件 API 版本定义在 `twipsybot.plugin.PLUGIN_API_VERSION`。对公共事件、结果、上下文或服务契约做不兼容修改时：

1. 提升 API 版本。
2. 更新插件加载校验和开发文档。
3. 同步迁移全部内置插件。

数据库结构变更应提供可重复验证的版本化迁移和覆盖从旧版本升级的测试。
