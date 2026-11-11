# 文档目录 - 从哪一份读起

文档按**读者**分成四类，随应用一起装到机器上。

**运维助手只读操作指南**：`doc/usage/`、`doc/reference/`、以及模块自己的 `doc.md`。
开发指南（`doc/dev/`）与运维手册（`doc/ops/`）不在它的清单里；二次开发在开发者自己的环境里进行。

| 目录 | 给谁看 | 写什么 |
| --- | --- | --- |
| [usage/](usage/) | 用这套系统的人 | 怎么用：一件事一份，按手上要办的事找 |
| [reference/](reference/) | 使用者与开发者 | 术语、接口、API 令牌 |
| [ops/](ops/) | 维护这台机器的人 | 部署、版本、日志、备份 |
| [dev/](dev/) | 改这套代码的人（**助手读不到**） | 结构、模块开发、界面规范 |
| `builtin/modules/<id>/doc.md` | 用某个模块的人 | 这个模块的页面与现场操作（不走这里，跟着模块走） |

## 使用

一份只讲一件事。

| 文件 | 一句话 |
| --- | --- |
| [usage/start.md](usage/start.md) | 开始用：注册与审核、侧栏有哪些页、顶栏、手机上 |
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

## 参考

| 文件 | 一句话 |
| --- | --- |
| [reference/glossary.md](reference/glossary.md) | 术语表：名单 / 报名 / 预设 / 状态词 / 角色 |
| [reference/ui-naming.md](reference/ui-naming.md) | 界面描述写法：位置词、按钮与图标怎么写 |
| [reference/api.md](reference/api.md) | 接口：路由家族、浏览器侧鉴权、事件流、错误约定 |
| [reference/api-tokens.md](reference/api-tokens.md) | API 令牌：怎么建、怎么用、三档范围、做不到什么、审计 |
| [reference/api-tables.md](reference/api-tables.md) | 表格导入导出接口：导出与导入的地址与规矩 |
| [reference/api-service.md](reference/api-service.md) | 服务有效性接口：只发警告、不发指令的那一族 |
| [reference/api-shortlink.md](reference/api-shortlink.md) | 短链接口：生成、列出、删除，与拒绝的情况 |
| [dev/ui-spec.md](dev/ui-spec.md) | 界面规范（开发指南）：标题与返回、卡片、表格、控件、反馈、措辞、抽屉、检查清单 |

## 运维

| 文件 | 一句话 |
| --- | --- |
| [ops/deploy.md](ops/deploy.md) | 部署、版本号、服务脚本、日志、备份与还原 |

## 设计

| 文件 | 一句话 |
| --- | --- |
| [dev/architecture.md](dev/architecture.md) | 结构：三个数据库、路由家族、实时同步、权限层次、模块能力声明 |
| [dev/assistant.md](dev/assistant.md) | 运维助手的设计：为什么沿用账号权限、工具面、策略层 |
| [dev/modules.md](dev/modules.md) | 模块开发指南：module.json、能力声明（features）、页面与屏幕 |
| [dev/module-interface.md](dev/module-interface.md) | 模块接口：平台与模块各管什么、接口版本与兼容政策、动作声明 |

## 写文档的约定

**给谁写**

1. **写给读者，不写给作者**：先问"谁在什么情况下会打开这一页"，再决定写什么。
   使用者手册不解释实现，设计文档不教怎么点按钮。
2. **一处说一处**：同一件事只在一份文件里写全，别处链接过去。一份文件只讲一件事，
   超过两屏就该拆——读的人不必为了查一句把整块吞下去。
3. **界面上的字与文档里的字要一致**：按钮叫「创建报名」，文档里就不写"新建报名"。
   术语以 [reference/glossary.md](reference/glossary.md) 为准。
4. **写完一句检查一句**：书面语、简洁、不用感叹号；不写"界面行为"（一眼就看得出的东西）。

**怎么写**

5. 标题写成 `# 名称 - 一句话`，破折号后面说这份文档覆盖什么。
6. 章节照读者要答案的顺序：描述、快速开始、参考、行为说明、注意事项、限制与路线图、相关文档。
7. 链接写相对路径，链接文字里不加反引号，只链确实存在的文件。
8. 每份文档最后一节是「相关文档」，只列接着要读的那几份。
9. 标识符要能在代码里找到：大范围改名之后整篇核对一遍。

**不写什么**

10. 实现依赖链与内部机制（"本页建立在事件流之上"这类）。
11. 验证、测试与调优过程——那是工作记录，不是文档。
12. 历史沿革，以及与本次说明无关的背景。

**必须做**

13. **能验证的事实才写数字**：接口名、字段、上限、默认值要与代码一致；改了代码就回来改这里。
    文档与代码不一致时，读者会转向读代码并不再信任文档——要么同步，要么删掉。
14. **改代码与改它对应的文档放在同一次提交里。**
15. **界面照 [reference/ui-naming.md](reference/ui-naming.md) 的用词描述**：位置词固定（顶栏、侧栏、
    页面标题、页面副标题、页面右上角、内容区、卡片、表格、页签、抽屉、弹窗），只有图标的按钮
    补一句图标的样子，例如 `[导出这段对话（下载图标）]`。
16. **新增、修改或移除功能时，同步改使用者文档里对应的页面说明**——入口在哪一页、
    页面上有什么、按钮叫什么。运维助手看不到界面，只能照文档回答；文档没写的功能，
    它应当当作不存在，而不是推测一个位置。
17. 需要用于软件著作权登记的材料，取自 `usage/`（用户手册）与 `dev/`（设计说明书）两处，
    所以这两类要写全：功能做了什么、怎么用、边界在哪、为什么这样设计。
