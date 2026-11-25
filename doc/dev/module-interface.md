# 模块接口 - 声明格式、业务函数与页面接入

平台负责活动身份、成员席位、鉴权、通用资源及页面外壳。模块提供业务数据、动作与界面。开发和验证流程见[模块开发指南](modules.md)。

## 接口版本

`module.json` 的 `api` 为整数，省略时使用 1。平台支持的版本由 `core/modules.py` 中的 `SUPPORTED_API` 定义。模块的版本兼容性还受 `requires` 约束。

新增可选字段不改变接口版本。修改既有字段语义、增加必填条件或移除接口时，应评估接口版本并明确旧版本的兼容及迁移要求。

## module.json 字段

| 字段 | 用途 |
| --- | --- |
| `id` | 唯一模块 ID，例如 `nwuca.lottery`，不得使用保留前缀 `super.` |
| `name`、`version` | 显示名称及模块版本 |
| `api`、`requires` | 模块接口版本及平台版本约束 |
| `description`、`author`、`homepage`、`license`、`contact` | 可选的模块说明与维护信息 |
| `pages` | 后台页签，条目包含 `id` 和 `title` |
| `scripts` | 页签脚本前载入的共用脚本，省略 `.js` 后缀 |
| `settings` | 可在活动后台修改的模块设置，类型取自 `defaults.json` |
| `external` | 活动页面声明，识别 `board`、`screen`、`checkin` 和 `admin` |
| `features` | 平台能力声明，参见[能力声明](modules.md#能力声明) |
| `actions` | 写操作声明，参见[动作声明](#动作声明) |

创建活动时，平台读取 `schema.json` 中的 `fields` 生成参数表单并验证提交值。预设的参数也应符合该声明。

### 高危权限

`super.` 是平台保留的高危权限命名空间。当前支持 `super.anyshortlink`，允许短链指向活动范围外的地址。它与常规 `shortlink` 一并声明。

站长导入模块时，平台列出模块声明的高危权限。确认安装须使用 sudo 模式。模块启用后，受支持的声明生效。在模块说明中注明权限用途及影响。查看权限或停用模块的操作见[平台管理页](../usage/platform-pages.md#高危权限)。

### 打包与安装

ZIP 文件可直接包含模块文件，也可包含一层模块目录。`module.json` 须位于模块目录根部。上限为压缩包 2 MiB、解压总计 8 MiB、单文件 1 MiB 和 400 个文件。界面导入仅接受 `modules.EXT_ALLOWED` 中的扩展名，并拒绝符号链接。

当前 ZIP 导入不接受 `.sql`。需要发布 `schema.sql` 的受信模块可由部署维护人员放入实例的 `modules/`，再重启服务。手动安装应核对模块声明的高危权限。

## 业务函数

平台通过 `core/model.py` 按活动所用模块加载 `model.py`。模块按所用页面和能力提供相应函数。

| 函数 | 用途 |
| --- | --- |
| `ensure_seed(conn)` | 可选的初始化钩子。须可重复调用 |
| `snapshot(conn, cfg)` | 返回后台记录。使用搜索时，记录字段须匹配能力声明 |
| `stats(conn, cfg)` | 返回统计数据，字段由模块定义 |
| `present(conn, cfg)` | 返回公开展示数据 |
| `check_settings(conn, updates)` | 可选，在平台完成类型校验后验证设置 |
| `export_sheets(conn, cfg)` | 可选，返回包含 `name`、`text` 及可选 `primary` 的导出条目 |
| `action(name, ctx, payload)` | 实现模块声明的动作 |
| `self_checkin(conn, sid, name)` | 实现使用平台签到流程的模块的签到操作 |

导出条目以 `primary: True` 标记单独下载的主 CSV。平台负责 ZIP 打包。

### 数据表

平台初始化活动数据库，随后执行模块提供的 `schema.sql`。表声明须可重复执行，建议为模块表使用专属前缀。涉及既有数据的结构变更应由模块明确处理并验证升级场景。

## 动作声明

平台在 `POST /<token>/api/do/<name>` 分发模块动作。

```json
{
  "example": {
    "title": "更新记录",
    "args": {"record": {"type": "int", "required": true}},
    "seat": "member",
    "unlock": true,
    "finalized": "block",
    "target": "record:{record}"
  }
}
```

| 字段 | 默认值 | 用途 |
| --- | --- | --- |
| `title` | 动作名 | 面向用户的操作名称及默认审计说明 |
| `args` | `{}` | 参数定义，支持 `int`、`str`、`bool`、`float`、`json` 类型及 `required`、`max` |
| `seat` | `member` | 所需活动席位，可设为 `owner` |
| `unlock` | `false` | 是否须启用「解锁修改」 |
| `finalized` | `block` | 活动结算后是否允许执行，可设为 `allow` |
| `target` | `event` | 版本通知的对象模板，`{参数名}` 会替换为实际参数值 |

平台校验会话、席位、跨站请求防护及动作条件，并在事务中调用 `action()`。`ctx` 包含 `conn`、`cfg`、`event`、`account`、`seat`、`unlocked` 和 `now`。其中 `event` 包含 `seq`、`title` 和 `kind`。返回字典可包含 `audit`，用于补充操作日志。成功提交后由平台发布对象版本。

参数或业务校验失败时抛出 `errors.ApiError`，传入面向用户的说明及相应 HTTP 状态码。动作实现使用调用方提供的事务，不自行提交或发布版本号。

## 页面与资源

后台页签使用 `pages/<id>.html` 提供 HTML 片段。平台先加载 `scripts` 声明的共用脚本，再按页签顺序加载 `pages/<id>.js`。脚本从 `/<token>/module/<name>.js` 提供。

```js
window.MODULE.pages.main = {
  mount(ctx) {},
  refresh(state, tab) {},
  show(tab) {},
  count(state) {},
};
```

- `mount(ctx)` 初始化页签并注册事件。上下文包括 `LT`、`state`、`tab`、`page`、`reload` 和 `showTab`。
- `refresh(state, tab)` 接收平台重新加载的状态。`show(tab)` 在切入页签时调用，可返回 Promise。`count(state)` 可返回页签计数。
- `ctx.reload()` 重新读取状态，`ctx.showTab(id)` 切换页签。
- 可选的全局钩子包括 `MODULE.head.render(state)`、`MODULE.landing(state)` 和 `MODULE.unlockNote`，用于提供标题栏内容、默认页签和解锁提示。

### 活动页面

模块公示页和签到页可在 `external/` 提供完整 HTML 及对应资源，并在 `module.json` 中声明页面。公示页使用 `external/board.html`，签到页使用 `external/checkin.html`。活动后台由平台提供外壳，其中注入模块页签。建议明确声明 `external`，省略时加载器会采用默认页面声明。展示屏幕通过活动的 `screen_pack` 设置选择 `theme/<标识>/screen.html`，由展示屏幕分发器处理。

`GET /<token>/api/board` 返回公开活动设置 `config`、模块 `present()` 的结果以及 `serverTime`。`present` 中的业务字段由模块与页面共同约定。经典展示屏幕读取可选的 `screen` 对象，各区域为可选字段。公开展示结果仅应包含可公开的数据。

| 字段 | 经典展示屏幕读取的内容 |
| --- | --- |
| `subtitle`、`qr` | 副标题及各阶段的二维码说明 |
| `banner` | 统计项目，常用字段为 `label`、`value`、`of`、`tone` 和 `phase`。未指定 `phase` 时不限制展示阶段 |
| `strip` | 横向记录，包含 `label` 及 `items`。记录可提供 `title`、`name`、`note` 和样式类别 `kind` |
| `wall` | 列表区域，使用 `on` 控制显示，并通过 `items` 提供 `name`、`note` 和 `level` |
| `cards` | 包含 `title` 和 `body` 的卡片 |
| `reveal` | 揭示动画，使用 `key` 标识新结果。可提供 `candidates`、`value`，多个结果可通过 `items` 集中展示 |

渲染细节见 `assets/js/screen.js`，数据示例见内置模块的 `present()`。

## 停用与异常

平台在分发活动请求前检查模块状态。模块停用时，关联活动页面显示不可用提示，接口返回 HTTP 503。已打开的页面通过事件流接收状态变化。启用后可刷新页面恢复访问。

通过 `core/moduleguard.py` 执行的模块函数发生非业务异常时，平台记录异常并尝试重新加载模块。达到连续失败阈值（三次）时停用模块并记录审计。常规调用成功会清除累计失败计数。模块应使用 `ApiError` 报告预期的业务拒绝。

## 相关文档

- [模块开发指南](modules.md)
- [项目架构](architecture.md)
- [界面规范](ui-spec.md)
