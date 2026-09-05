---
title: TwipsyBot 开发
description: 了解 TwipsyBot 架构、插件 API、测试流程和项目维护方式。
---

# 开发

本指南面向需要修改 TwipsyBot、开发插件或参与项目维护的开发者。项目使用 Python 3.11+，核心保持轻量，扩展功能优先通过插件实现。

## 内容

- [开发环境](setup.md)：安装开发依赖并运行项目检查。
- [架构](architecture.md)：理解启动流程、事件处理和核心模块边界。
- [插件开发](plugins.md)：创建插件并使用事件、服务和存储 API。
- [测试与质量](testing.md)：运行测试、Ruff、Pyright 和 pre-commit。
- [发布与维护](maintenance.md)：管理依赖、版本、镜像和兼容性。

## 从哪里开始

修改核心行为前，先定位负责该行为的 flow、engine 或 client。新增独立功能时，优先评估插件 Hook 是否足够；只有需要改变共享生命周期、协议适配或数据模型时，才修改核心。

项目的稳定扩展边界位于 `twipsybot.plugin`。插件不应导入内部 engine、flow、client 或数据库实现。