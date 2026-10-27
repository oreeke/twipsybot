---
title: Web 联网检索
description: 使用 Web 插件让 AI 联网检索并读取网页链接。
---

# Web：联网检索

提供 `web_search` 与 `web_fetch` 两个[工具](../features/interactions.md#工具)，由模型按需联网检索、读取网页正文。关注机器人的用户与管理员可用。

## 配置

```yaml
plugins:
  web:
    enabled: true
    provider: searxng
    endpoint: http://searxng:8080
    max_results: 5
    fetch_results: false
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `provider` | `searxng` | 检索后端：`searxng` 或 `deepseek` |
| `endpoint` | 空 | 后端地址，`searxng` 必填；`deepseek` 留空使用官方地址 |
| `api_key` | 空 | `deepseek` 必填，与 AI 的密钥相互独立 |
| `prompt` | 内置 | 附在检索结果前的说明 |
| `empty_prompt` | 内置 | 检索无结果时的提示 |
| `language` | `auto` | 检索语言，如 `zh-CN`；留空不指定，仅 `searxng` 生效 |
| `max_results` | `5` | 返回的结果条数，1–10 |
| `fetch_results` | `false` | 读取结果的网页正文；`searxng` 逐条抓取 `max_results` 条结果，`deepseek` 在搜索请求中使用 `need_content` |
| `max_chars` | `6000` | 单次返回的总字符上限 |
| `max_bytes` | `1MB` | 单个网页下载上限，支持 `KB`、`MB` 等单位；`deepseek` 不适用 |
| `timeout` | `10` | 单次请求超时（秒） |
| `cache_ttl` | `600` | 检索与网页缓存秒数，`0` 关闭 |
| `allow_domains` | 空 | 每行一个域名，非空时仅允许这些域名及其子域名 |
| `block_domains` | 空 | 每行一个域名，禁止这些域名及其子域名 |

## 使用

- 对话中提到需要查询的内容或给出链接，模型会自行调用；也可用 `/search <问题>`、`/fetch <链接>` 强制。
- 检索失败或无结果时，模型会如实说明并基于已有知识回答。
- 检索结果只用于本次请求，不写入聊天历史。
- 模型引用来源时，会在回复末尾用编号超链接标注，如 `1 2`，点击编号即可打开对应网页；是否标注由模型决定。

## SearXNG

`provider: searxng` 需启用 JSON 输出，否则会返回 403。在 `settings.yml` 中：

```yaml
search:
  formats:
    - html
    - json
```

仅供机器人内部访问时，建议关闭或放行 `server.limiter`，否则机器人请求可能被限流。示例：

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    volumes:
      - ./searxng:/etc/searxng
```

## DeepSeek

使用 DeepSeek 的 [Search / Fetch 接口](https://api-docs.deepseek.com/zh-cn/guides/search)，搜索与网页抓取均由其服务端完成：

```yaml
plugins:
  web:
    enabled: true
    provider: deepseek
    api_key: sk-xxx
```

- 抓取由 DeepSeek 服务器访问目标网址；`allow_domains` 与 `block_domains` 仍然生效。

## 安全

- 网页抓取仅访问公网地址，重定向同样校验，`endpoint` 不受限。Fake-IP 代理需配置 [`system.allow_nets`](../reference/configuration.md#链接下载安全)。
- 网页内容仅作参考，并要求模型不执行其中的指令，但无法完全杜绝提示注入；读取网页或检索后，该轮不再提供发帖、生成图片等有影响的工具。公开服务建议配合 `allow_domains`。
- 模型发出的检索词与链接会被送往检索后端及目标网址，链接由机器人所在服务器直接访问；使用 `deepseek` 时，问题与链接都会发送给 DeepSeek，由其服务器访问。
