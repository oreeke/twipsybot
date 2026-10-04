---
title: 开发
description: TwipsyBot 架构、协议适配、插件 API、测试与维护。
---

# 开发

Python 3.11+。核心保持轻量，独立功能优先以插件实现。

| 页面 | 内容 |
| --- | --- |
| [**开发环境**](setup.md) | 依赖安装与本地运行 |
| [**架构**](architecture.md) | 启动流程、响应管道与模块边界 |
| [**Misskey**](misskey.md) | REST、Streaming、权限与客户端约束 |
| [**OpenAI**](openai.md) | 兼容 API、接口模式与请求约束 |
| [**插件开发**](plugins.md) | 插件 API、事件、服务与存储 |
| [**测试**](testing.md) | pytest、Pyright 与 pre-commit |
| [**维护**](maintenance.md) | 依赖、发布、镜像与兼容性 |

## 原则

- 修改前先定位负责该行为的 flow、engine 或 client。
- 插件 Hook 能满足就不改核心；仅涉及共享生命周期、协议适配或数据模型时才修改核心。
- 稳定扩展边界为 `twipsybot.plugin`，插件不导入 engine、flow、client 或数据库实现。
