---
title: Web 联网检索
description: 使用 Web 插件让 AI 在回复前联网检索，并读取消息中的链接。
---

# Web：联网检索

在默认 AI 回复前联网检索，并抓取消息中的链接，将结果作为参考资料交给模型。仅补充上下文，不接管回复，因此不影响历史、限流与其他插件。

## 配置

```yaml
plugins:
  web:
    enabled: true
    provider: searxng
    endpoint: http://searxng:8080
    always_on: false
    max_results: 5
    fetch_top: 0
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `provider` | `searxng` | 检索后端，目前仅支持 `searxng` |
| `endpoint` | 空 | 后端地址，`searxng` 必填，插件仅检索此实例 |
| `always_on` | `false` | 开启后每条消息都先联网检索；关闭则仅 `/web` 触发 |
| `prompt` | 内置 | 附在检索结果前的说明 |
| `empty_prompt` | 内置 | 手动 `/web` 无结果时的提示 |
| `rewrite_prompt` | 内置 | `query_mode: rewrite` 时改写关键词的系统提示词 |
| `language` | `auto` | 检索语言，如 `zh-CN`；留空不指定 |
| `max_results` | `5` | 注入的结果条数，1–10 |
| `fetch_top` | `0` | 抓取前 N 条结果的正文（0–3）；0 仅使用摘要，最省 token |
| `query_mode` | `raw` | `rewrite` 先让模型改写为搜索关键词，多一次模型调用，推理模型下明显更慢，建议保持 `raw` |
| `min_chars` | `4` | `always_on` 下，去掉链接与提及后少于该字数则不检索 |
| `max_chars` | `6000` | 注入参考资料的总字符上限 |
| `max_bytes` | `1MB` | 单个网页下载上限，支持 `KB`、`MB` 等单位 |
| `timeout` | `10` | 单次请求超时（秒） |
| `cache_ttl` | `600` | 检索与网页缓存秒数，`0` 关闭 |
| `allow_domains` | 空 | 每行一个域名，非空时仅允许这些域名及其子域名 |
| `block_domains` | 空 | 每行一个域名，禁止这些域名及其子域名 |
| `trusted_proxy` | 空 | 每行一个 CIDR，这些网段视为公网，用于 Fake-IP 代理等环境，见[安全](#安全) |

## 使用

- **手动**：`always_on` 关闭时，在提及、私聊或群聊（需提及）中以 `/web <问题>` 发起，如 `@bot /web 今天有什么科技新闻`。`/web` 前缀不会发给模型。
- **常开**：`always_on` 开启后，每条消息都先检索，无需前缀；`/web` 仍可用。
- **链接**：只要插件启用，消息中的链接（最多 3 个）都会被抓取，与 `always_on` 无关。
- 检索失败或无结果时，手动 `/web` 会让模型如实说明并基于已有知识回答，常开模式则按普通回复处理。
- 参考资料只附加在本次请求中，不写入聊天历史。
- 模型引用来源时，会在回复末尾用编号超链接标注，如 `1 2`，点击编号即可打开对应网页；是否标注由模型决定。

`/web` 不是管理命令，所有可对话用户均可使用。

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

## 安全

- 网页抓取仅允许 `http` / `https`，每次连接与重定向（最多 3 次）都会拒绝回环、内网、链路本地等非公网地址，防止通过链接访问内网服务。`endpoint` 由管理员配置，不受此限制。
- 使用 Fake-IP 代理（域名被解析为保留网段）时，网页会因解析到非公网地址而无法读取。此时在 `trusted_proxy` 中填入代理使用的网段。仅填写你信任的网段，填入内网网段会让机器人可访问这些内网地址。
- 网页内容被标记为不可信数据，并要求模型不执行其中的指令，但无法完全杜绝提示注入；公开服务建议配合 `allow_domains`。
- 用户的问题会发送到检索后端及其上游搜索引擎，链接会被机器人所在服务器直接访问。

## 排查

- 无任何检索：确认插件已启用、`endpoint` 正确（日志有 `Web plugin provider unavailable` 则后端配置无效），且 `always_on` 或 `/web` 条件满足。
- 日志出现 `Web search failed`：检查后端是否可达；SearXNG 还需确认已启用 JSON 输出、未被限流。
- 链接未被读取：地址为内网（Fake-IP 代理环境见上文 `trusted_proxy`）、被域名规则拦截、非文本网页，或超时 / 超过 `max_bytes`；需要登录或脚本渲染的页面无法读取。
