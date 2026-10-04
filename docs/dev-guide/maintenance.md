---
title: 维护
description: 维护 TwipsyBot 依赖、版本发布、Docker 镜像与兼容性。
---

# 维护

版本以 `pyproject.toml` 的 `project.version` 为准。用户可见的行为、配置、命令或兼容性变化须同步更新文档。

## 依赖

```bash
uv add <package>
uv add --optional dev <package>
uv lock --check
```

运行依赖使用精确版本。升级后执行全部检查，并确认 Docker 构建仍能以二进制 wheel 与哈希锁定安装。

## 发布

1. 更新 `project.version` 与受影响文档。
2. 同步 `uv.lock` 并通过全部检查。
3. 合并到 `main`，工作流自动创建 `v<version>` 标签。

不要手工创建其他格式的标签，也不要复用已有版本。

## Docker 镜像

`vX.Y.Z` 标签触发 `linux/amd64`、`linux/arm64` 多架构构建，发布完整版本、主次版本与 `latest` 标签。

```bash
docker build -t twipsybot:dev .
docker run --rm twipsybot:dev --help
```

多阶段构建：builder 生成 wheel，运行阶段以非 root 的 `appuser` 运行。新增运行时文件时确认已复制进镜像且权限正确。

## 兼容性

不兼容地修改插件公共事件、结果、上下文或服务契约时：

1. 提升 `twipsybot.plugin.PLUGIN_API_VERSION`。
2. 更新插件加载校验与开发文档。
3. 迁移全部内置插件。

数据库结构变更需提供版本化迁移，并测试从旧版本升级。
