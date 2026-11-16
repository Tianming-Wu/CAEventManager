# 文档目录 - 从哪一份读起

文档按**读者**分为四类，随应用一并部署。

**运维助手操作指南**：`doc/usage/`、`doc/reference/`、以及模块自己的 `doc.md`。
开发指南（`doc/dev/`）与运维手册（`doc/ops/`）不包含在内。

| 目录 | 读者 | 内容 |
| --- | --- | --- |
| [usage/](usage/) | 用户 | 怎么用：一件事一份，按手上要办的事找 |
| [reference/](reference/) | 用户和开发者 | 术语、接口、API 令牌 |
| [ops/](ops/) | 服务维护者 | 部署、版本、日志、备份 |
| [dev/](dev/) | 开发者 | 结构、模块开发、界面规范 |
| `builtin/modules/<id>/doc.md` | 用户 | 模块的特定文档 |

## 使用 (usage)

| 文件 | 概述 |
| --- | --- |
| [usage/about.md](usage/about.md) | 关于：版本号怎么读、仓库与许可证、作者与反馈 |
| [usage/start.md](usage/start.md) | 开始使用：注册与审核、侧栏有哪些页、顶栏、手机上 |
| [usage/account.md](usage/account.md) | 账号与角色：三种角色、登录名、账户信息、敏感操作、界面设置 |
| [usage/events.md](usage/events.md) | 活动：我的活动、创建、邀请成员、移出、存档、活动后台 |
| [usage/event-site.md](usage/event-site.md) | 活动现场：地址、公示页、大屏、签到、活动设置、日志、模块页面在哪 |
| [usage/rosters.md](usage/rosters.md) | 名单：新建、导入、成员、可见性、写权限 |
| [usage/signups.md](usage/signups.md) | 报名：链接、限制范围、时间与名额、重复提交、字段、主题、二维码 |
| [usage/output.md](usage/output.md) | 导出与打印：CSV 与 xlsx、打印名单、打印报名表 |
| [usage/presets.md](usage/presets.md) | 预设：存一份配置、三处陈列、与模块的关系 |
| [usage/approval.md](usage/approval.md) | 审批：注册申请、写入审批、活动申请 |
| [usage/server.md](usage/server.md) | 服务器与审计：机器占用、运维操作、审计日志 |
| [usage/shortlinks.md](usage/shortlinks.md) | 短链：谁能生成、有效期与次数、删除、超管 |
| [usage/platform-pages.md](usage/platform-pages.md) | 平台管理页：用户管理、模块管理、展示屏幕 |
| [usage/notices.md](usage/notices.md) | 通知与服务提示：铃铛、紧急弹窗、降级提示、自己排一条 |
| [usage/assistant.md](usage/assistant.md) | 运维助手：先配模型、怎么问、它能做什么、对话存档 |
| [usage/assistant-prompt.md](usage/assistant-prompt.md) | 助手的平台提示词：文件位置、可以写的变量、例子 |
| [usage/theme.md](usage/theme.md) | 界面设置：平台的显示外观 |
| [usage/sudo.md](usage/sudo.md) | sudo 模式：敏感操作认证、十分钟内不再问 |

## 参考 (ref)

| 文件 | 概述 |
| --- | --- |
| [reference/glossary.md](reference/glossary.md) | 术语表：名单 / 报名 / 预设 / 状态词 / 角色 |
| [reference/ui-naming.md](reference/ui-naming.md) | 界面描述写法：位置词、按钮与图标怎么写 |
| [reference/api.md](reference/api.md) | 接口：路由家族、浏览器侧鉴权、事件流、错误约定 |
| [reference/api-tokens.md](reference/api-tokens.md) | API 令牌：怎么建、怎么用、三档范围、做不到什么、审计 |
| [reference/api-tables.md](reference/api-tables.md) | 表格导入导出接口：导出与导入的地址与规矩 |
| [reference/api-service.md](reference/api-service.md) | 服务有效性接口：只发警告、不发指令的那一族 |
| [reference/api-shortlink.md](reference/api-shortlink.md) | 短链接口：生成、列出、删除，与拒绝的情况 |
| [dev/ui-spec.md](dev/ui-spec.md) | 界面规范（开发指南）：标题与返回、卡片、表格、控件、反馈、措辞、抽屉、检查清单 |

## 运维 (ops)

| 文件 | 概述 |
| --- | --- |
| [ops/deploy.md](ops/deploy.md) | 部署、版本号、服务脚本、日志、备份与还原 |

## 设计 (dev)

| 文件 | 概述 |
| --- | --- |
| [dev/architecture.md](dev/architecture.md) | 结构：三个数据库、路由家族、实时同步、权限层次、模块能力声明 |
| [dev/assistant.md](dev/assistant.md) | 运维助手的设计：为什么沿用账号权限、工具面、策略层 |
| [dev/modules.md](dev/modules.md) | 模块开发指南：module.json、能力声明（features）、页面与屏幕 |
| [dev/module-interface.md](dev/module-interface.md) | 模块接口：平台与模块各管什么、接口版本与兼容政策、动作声明 |

## 文档编写约定

**目标**

1. **写给读者，不写给作者**：按照"谁在什么情况下会打开这一页"，再决定写什么。
   用户手册不解释实现，设计文档不教怎么操作。
2. **一处说一处**：同一件事只在一份文件里写全。不直接相关的细节使用链接指向最相关的文档。一份文件专注于一个方面，
   避免文档过长，过长应该拆分，以方便助手阅读。
3. **界面文案与文档中保持一致**：按钮写作「创建报名」，文档里就不应写作"新建报名"。
   术语以 [reference/glossary.md](reference/glossary.md) 为准。
4. **写完一句检查一句**：书面语、简洁、不用感叹号；不包含"界面行为"等操作时直观可见的内容。

**写法**

5. 标题写作 `# 名称 - 概述`，在破折号后简要概括这篇文档覆盖的内容。
6. 章节照读者阅读的逻辑顺序：描述、快速开始、参考、行为说明、注意事项、限制与路线图、相关文档。
7. 链接使用相对路径，链接文字里不加反引号，只指向确实存在的文件。要指到某一节可在路径后拼接
   `#标题`，文档内部跳转则写 `#标题`——锚点取标题正文，转小写、空格换连字符、去掉标点。可以使用
   VS Code 编辑器等获得正确的锚点名称。
   图片写作 `![说明](图片名)`，图跟文档放在一起；只认 png / jpg / webp / gif / svg
8. 每份文档最后一节是「相关文档」，只列接着要读的那几份。
9. 使用的标识符必须能在代码中找到。大范围重构之后必须完整检查。

**避免**

10. 不应包含实现依赖链与内部机制（"本页建立在事件流之上"等技术细节）。
11. 不得包含任何与文档主题无关的内容，包括但不限于历史沿革、背景信息等。
12. 不得包含任何开发或者部署环境的信息，包括但不限于实例名称、部署路径、服务器地址、用户名等。

**必做**

13. **能验证的事实才写数字**：接口名、字段、上限、默认值需与代码一致；修改代码时应同步更新文档。
14. **代码更改与对应的文档更改需一同提交**
15. **界面描述用词遵循 [reference/ui-naming.md](reference/ui-naming.md)**：使用统一的位置描述词（顶栏、侧栏、
    页面标题、页面副标题、页面右上角、内容区、卡片、表格、页签、抽屉、弹窗），仅图标按钮
    补充对图标样式的简单描述，例如 `[导出这段对话（下载图标）]`。
16. **新增、修改或移除功能时，同步改用户文档里对应的页面说明**——入口在哪一页、
    页面上有什么、按钮叫什么。运维助手看不到界面，只能照文档回答；文档没写的功能，
    它应当当作不存在，而不是推测一个位置。
17. 需要用于软件著作权登记的材料，取自 `usage/`（用户手册）与 `dev/`（设计说明书）两处，
    所以这两类要写全：功能做了什么、怎么用、边界在哪、为什么这样设计。
