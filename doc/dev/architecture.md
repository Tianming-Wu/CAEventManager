# 项目架构 - 进程、存储、权限与同步

平台使用 Python 标准库提供 HTTP 服务，前端使用原生 JavaScript。部署及启动方式参见[部署与运维](../ops/deploy.md)。

## 进程与路由

`app.py` 使用 `ThreadingHTTPServer` 处理面板、文档、短链和活动请求。活动按路径中的访问凭据分发，外部访问地址通过 `public_base_prefix` 配置。接口路径参见[接口参考](../reference/api.md#路由分组)。

| 组件 | 职责 |
| --- | --- |
| `core/paths.py`、`core/localconfig.py` | 应用路径及实例配置 |
| `core/panel.py`、`core/lists.py` | 平台账号、活动索引、名单与报名的数据访问 |
| `core/db.py`、`core/eventfs.py` | 活动数据库和归档 |
| `core/modules.py`、`core/model.py` | 解析模块声明、加载模块和分发模型调用 |
| `core/bus.py` | 对象版本与事件流 |
| `core/assistant.py`、`core/policy.py` | 运维助手及工具准入 |
| `core/health.py`、`core/safemode.py`、`core/startguard.py` | 服务状态提示、安全模式及启动自检 |
| `core/backup.py` | 备份、恢复、留存与异地副本 |

## 数据

下列路径以默认数据目录为例。

| 路径 | 内容 |
| --- | --- |
| `data/panel.sqlite3` | 账号、会话、令牌、活动索引、名单与报名索引、通知、平台审计、助手存档与短链 |
| `data/lists.sqlite3` | 人员、名单成员及报名提交记录 |
| `data/events/<seq>/data.sqlite3` | 活动配置及模块业务数据 |
| `data/events/<seq>/audit.sqlite3` | 活动操作日志 |
| `data/events/<seq>/meta.json` | 活动元数据 |

SQLite 使用 WAL。业务写入通过事务处理。归档与备份的操作方式参见[活动管理](../usage/platform-pages.md#活动管理)及[备份与恢复](../ops/deploy.md#备份与恢复)。

### 活动标识

`seq` 用于服务端查询、选库、同步和管理接口。活动访问凭据是活动对外路径中的随机字符串。通过 `panel.event_seq()` 与 `panel.event_token()` 在两种标识之间转换。

### 随机数据

在 `core/rng.py` 中集中生成随机数据。随机整数使用拒绝采样。活动访问凭据、会话、短链及其他需要随机值的功能复用该模块。可通过 `rng.source()` 查询当前使用的随机源。

## 权限

平台接口按账号角色校验访问权限。活动后台另按当前活动的成员席位授权。名单可见性与写入权限分别处理。

归档及永久删除的界面入口参见[平台管理页](../usage/platform-pages.md)。提权操作由服务端校验当前会话的 sudo 状态。前端的 `confirmHighRisk()` 只负责确认流程，具体权限以服务端判定为准。

### 审计

平台审计字段为 `ts`、`actor`、`action`、`target`、`detail`。`panel.audit()` 通过 `panel.actor_key()` 统一操作人标识。账号操作以 `uid` 记录，界面显示通过 `panel.with_actor_labels()` 生成。使用 `panel.audit_note()` 生成结构化说明，格式为 `key: value`，多个字段以逗号分隔。

账号编号 `uid` 用于标识对外显示的账号，分配后不重复使用。`id` 是数据库主键，`username` 是登录凭据。需要公开显示账号名称时，使用 `panel.public_name()`。

### 账号选择

面板和活动后台复用 `assets/js/people.js` 及 `assets/css/people.css`。候选载荷由 `panel.person_view()` 生成，包括 `id`、`uid`、`name`、`label` 和 `avatar`。面板的 `/api/panel/people` 按 `scene` 校验当前操作权限。活动侧使用 `/<token>/api/admin/people`。

## 实时同步

`core/bus.py` 以对象为单位维护版本，例如 `account:7`、`event:3` 和 `event:1:members`。写入完成后发布版本变化。客户端订阅[事件流](../reference/api.md#事件流)，按受影响对象更新数据。

并发写入通过 `X-Rev-Map` 传递客户端已知版本。服务端检测相关对象是否已更新。对支持幂等检查的接口，目标状态已经满足时可成功返回；发生冲突时返回 HTTP 409。

## 前端

- `assets/js/common.js` 提供面板共用函数，`assets/js/event.js` 提供活动侧共用函数。
- `assets/js/markdown.js` 提供共用 Markdown 渲染。`assets/js/docs.js` 管理文档查看器。
- `pages/` 包含服务端页面模板。`theme/` 提供展示屏幕外观包。
- 账号、审计日志等较长列表由服务端分页及搜索。页面布局和状态文案参见[界面规范](ui-spec.md)。

模块业务逻辑、能力和页面资源参见[模块接口](module-interface.md)。

## 提交信息

标题概括变更内容，正文可按变更项列出要点。使用书面语，省略理由、过程、测试结果及作者信息。提交范围与验证要求参见[仓库协作约定](../../AGENTS.md)。

## 服务器特性

在 `core/features.py` 的 `FEATURES` 中注册状态检查函数。函数返回 `id`、`name`、`ok` 和 `detail`。其中 `ok` 表示是否可用，`name` 和 `detail` 分别对应「服务器特性」卡片中的「特性」列和「状态」列。检查函数应说明依赖缺失时对使用者的影响。单项检查失败时，其余特性的状态仍可显示。

## 相关文档

- [模块开发指南](modules.md)
- [运维助手的设计](assistant.md)
- [部署与运维](../ops/deploy.md)
