## Iincho 插件

没错我是委員長，每隔一段时间汇总本地时间线帖子，发布脱敏违规概览，并把热点和违规帖子 ID 私信给管理员（打小报告）。

### 启用

设置 `timeline.local: true` 并重启机器人；然后在 `twipsybot cfg` 中启用 Iincho，或编辑 `data/settings.yaml`：

```yaml
plugins:
  iincho:
    enabled: true
    admin_ids:
      - "9abcdef012345678"
```

`admin_ids` 填写接收小报告的 Misskey 用户 ID，可配置多个，留空不发送提醒。发现疑似违规样本时，Iincho 会私信发送原帖 ID，单个管理员发送失败不影响其他管理员或摘要发布。

Iincho 使用固定容量均匀抽样，不保存帖子内容，不补采离线数据，不处理图片。
通用生成模型只总结热点趋势，仅在配置 `admin_ids` 且发现疑似违规时调用。文本风险审查可通过 `moderation.provider` 选择：

- `openai`（默认）：使用主配置端点的 `/moderations` 与 `omni-moderation-latest` 批量审核，自定义兼容端点需要支持该接口
- `cloudflare`：使用 Cloudflare Workers AI `@cf/meta/llama-guard-3-8b` 逐篇审核，独立于主配置，需填写 `cf_account_id` 和具备 Workers AI Read 权限的 `cf_api_token`，按 token 计费

周期从插件启动时开始计算。有效帖子不足、趋势生成或内容审核失败、发布失败时跳过本周期，不补发。

风险结果只表示模型对抽样帖子的分类信号，不代表人工裁定或全量审核，疑似违规原文和用户身份不会写入成帖。
