## 雷达插件

### 功能描述

与天线推送的帖子互动（反应、回复、转发、引用）

### 使用方法

复制 `config.yaml.example` 为 `config.yaml` 并修改配置<br>
在实例登录机器人账号 -> 添加天线 -> 设置名称（不要空格）和过滤条件并保存<br>
确保主配置已启用时间线 `bot.timeline.enabled: true` 并在 `bot.timeline.antenna_ids` 中填入天线名<br>
机器人将接收符合天线设置的帖子并与之互动，不需要订阅其他时间线
