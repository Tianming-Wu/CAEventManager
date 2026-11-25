# 助手的平台提示词 - 配置附加要求

部署维护人员可在数据目录中维护 `assistant_prompt.md`，为运维助手添加名称、语气等要求。也可通过 `LOTTERY_ASSISTANT_PROMPT` 指定文件。文件保存后，下一条消息即采用新内容。附加内容最多 20 KB，超出的部分会被截断。

## 可用变量

| 占位符 | 替换内容 |
| --- | --- |
| `${user.username}` | 登录名 |
| `${user.name}` | 显示名，未填写时使用登录名 |
| `${user.role}` | 角色名称 |
| `${user.roleKey}` | 角色标识（`owner`、`superadmin` 或 `admin`） |
| `${user.id}` | 当前账号的内部标识 |
| `${panel.name}` | 平台名称 |
| `${panel.version}` | 平台版本 |
| `${tools}` | 当前账号可用的工具名称 |
| `${time.date}` | 当前日期 |
| `${time.time}` | 当前时刻 |
| `${time.now}` | 当前日期与时刻 |

无法识别的占位符保留原文。

## 内容要求

附加提示词会发送给配置的模型服务。请勿包含密码、令牌、个人身份信息或其他敏感信息。

## 相关文档

- [运维助手](assistant.md)
