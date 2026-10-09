---
title: Iincho 本地时间线观察
description: 使用 Iincho 抽样 Misskey 本地时间线，发布内容风险概览。
---

# Iincho：本地时间线观察

定期均匀抽样本地时间线，发布内容风险概览。配置 `admin_ids` 且发现疑似违规时，额外生成热点趋势并私聊管理员（含相关帖子 ID）。公开概览不含原帖、用户或帖子 ID。

## 前置条件

- `timeline.local: true`，需重启。
- 审核后端二选一：
  - `openai`（默认）：主端点支持 `/moderations` 与 `omni-moderation-latest`。
  - `cloudflare`：账户 ID 与具备 Workers AI Read 权限的令牌，使用 `@cf/meta/llama-guard-3-8b`，不依赖主端点。
- 配置 `admin_ids` 时，文本模型需支持 JSON Object 输出。

## 配置

```yaml
plugins:
  iincho:
    enabled: true
    interval: 1h
    min_notes: 10
    sample_size: 100
    max_input_chars: 24000
    max_tokens: 2000
    temperature: 0.2
    local_only: true
    admin_ids:
      - "9abcdef012345678"
    moderation:
      provider: openai
      cf_concurrency: 4
```

Cloudflare 凭据存于 `data/secrets.yaml`：

```yaml
plugins:
  iincho:
    moderation:
      cf_account_id: "0123456789abcdef0123456789abcdef"
      cf_api_token: "..."
```

| 字段 | 说明 |
| --- | --- |
| `interval` | 报告周期，至少 5 分钟，从插件启动起算 |
| `min_notes` | 生成报告所需最少有效样本 |
| `sample_size` | 每周期最多样本数，不小于 `min_notes` |
| `max_input_chars` | 送入趋势模型的文本上限 |
| `local_only` | 报告不联合，建议保持 `true` |
| `admin_ids` | 接收私信报告的用户 ID，列表或逗号、空格分隔 |
| `moderation.cf_account_id` | Cloudflare 账户 ID |
| `moderation.cf_api_token` | Cloudflare 帐户 API 令牌 |
| `moderation.cf_concurrency` | Cloudflare 并发数 |

`prompt` 与 `system_prompt` 留空使用内置提示。

## 数据范围

- 仅处理运行期间收到的文本帖子，跳过自身帖子；不处理图片，不补采历史。
- 固定容量均匀抽样，不保存正文；送入趋势模型前替换 URL 与提及。
- 风险类别：骚扰攻击、仇恨歧视、色情内容、涉未成年、暴力威胁、自伤风险、违法活动、诽谤隐私。Llama Guard 不识别骚扰攻击，OpenAI 不识别诽谤隐私。
- 样本不足或审核、趋势生成、发布失败时跳过本周期且不补发。

::: warning
风险数字是模型对抽样文本的分类信号，不是人工裁定，也不代表整个时间线。仅用于观察趋势，不应作为封禁、处罚或用户画像依据。
:::
