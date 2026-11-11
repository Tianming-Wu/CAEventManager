# 模块接口 - 平台与模块各管什么

平台的代码里不出现任何玩法名词（奖票、任务链、抽奖）。这一份把「平台保证什么、模块必须提供什么」
定清楚，写的时候按它来，改的时候按它的兼容政策改。

接口版本见 [module.json 字段](#modulejson-字段)。这一版的接口版本是 **api 1**。

## 谁负责什么

| 事情 | 平台 | 模块 |
| --- | --- | --- |
| 活动、成员席位、邀请与移出 | 管 | — |
| 账号、角色、审核、sudo | 管 | — |
| 名单与报名（平台级的人） | 管 | 可调这批数据 |
| 通知、审计、短链、服务提示 | 管 | 用它的能力，不自己存 |
| 登录、会话、CSRF、并发冲突 | 管 | — |
| 页面外壳（顶栏、页签、卡片、表格样式） | 管 | 提供页面的内容与脚本 |
| 公示页、大屏的公共字段 | 管 | 提供展示数据 |
| 签到二维码与凭据、时限、presence cookie | 管 | 提供「记一次到场」的动作 |
| 玩法的数据表 | 建库时执行模块的 `schema.sql` | 声明表、写迁移 |
| 玩法怎么记人、发什么、抽不抽奖 | 不知道 | 全权负责 |
| 玩法页面上的按钮点什么 | 只管鉴权与事务 | 声明动作，实现动作 |

一句判断准则：**平台管「谁、在哪、能不能」，模块管「做什么、怎么算」。**

## 接口版本与兼容政策

`module.json` 的 `"api"` 是接口版本（整数，缺省按 1 算）。平台声明自己支持的版本
（`modules.SUPPORTED_API`），不支持的模块不出现在创建列表里。

改动规则：

| 改动 | 要不要动 `api` |
| --- | --- |
| 新增可选字段、新增动作、新增页面 | 不动：老模块照常跑 |
| 新增必填字段、改字段语义、改动作参数含义 | **+1**，并且在本文写明迁移要点 |
| 删除字段或动作 | **+1**，平台至少同时支持旧版本一个周期 |

平台升级时，旧版本的模块要能继续用：这是硬要求，`tools/unit_test.py` 里保留一组
「上一个 api 版本的模块声明」用来验证。

## module.json 字段

```json
{
  "id": "nwuca.lottery",
  "name": "奖票抽奖",
  "version": "1.0.0",
  "api": 1,
  "requires": ">=1.9",
  "description": "签到、任务进度、奖票与抽奖",
  "author": "…", "homepage": "…", "license": "…",
  "pages": [{"id": "ops", "title": "操作台"}],
  "external": ["board", "screen", "checkin", "admin"],
  "features": {"search": {}, "shortlink": {}},
  "actions": { … 见下 … },
  "board": {"title": "抽奖", "blocks": ["counters", "winners"]}
}
```

| 字段 | 谁用 | 说明 |
| --- | --- | --- |
| `id` | 平台 | `nwuca.lottery` 这种形状；`super.` 是保留前缀 |
| `name` | 平台 | 给人看的名字 |
| `version` | 平台 | 模块自己的版本，改就升 |
| `api` | 平台 | 接口版本，见上一节 |
| `requires` | 平台 | 需要的平台版本 |
| `pages` | 平台 | 申请哪些页签；`id` 任意小写串 |
| `external` | 平台 | 要哪些公开页面：`board` / `screen` / `checkin` / `admin` |
| `features` | 平台 | 能力声明（`search` / `shortlink` / 将来的 `super.*`） |
| `actions` | 平台 | 写操作声明，平台据此生成接口、鉴权、审计与版本号 |
| `board` | 平台 | 公示页/大屏要哪些块，见「展示数据」 |

## 模块必须提供的函数

模块在 `model.py` 里提供下面这些。平台只调这几个名字，别的一个都不认识。

### 生命周期

```python
def init_event(conn, params):
    """活动库刚建好、模块的表已建好时调用一次。params 是 schema.json 收上来的参数。"""

def ensure_seed(conn):
    """每次启动后的兜底：缺什么补什么，可重复调用。可以没有这个函数。"""
```

### 读

```python
def stats(conn, cfg):
    """给公示页/大屏与后台的一组数字，键名由 board 声明。没有就返回 {}。"""

def snapshot(conn, cfg):
    """后台「名单」页要的行：id / sid / name / grp / role / 状态字段由模块定，
    平台只按 features.search 的声明取值。"""
```

### 动作

模块不写 HTTP，只写动作：

```python
def action(name, ctx, payload):
    """执行一个动作。name 是 actions 里声明的名字，ctx 见下，payload 是参数。

    返回 dict：平台原样回给前端；想写审计就带上 "audit": "一句话"。
    出错抛 errors.ApiError（一句话 + 状态码）。
    """
```

`ctx` 由平台给：

```python
{"conn": conn, "cfg": cfg, "event": {"seq": 3, "title": "…", "kind": "nwuca.lottery"},
 "account": {"id": 7, "username": "laffey", "name": "吴天明", "role": "owner"},
 "seat": "owner", "unlocked": False, "now": 1794332130.1}
```

签到也是动作，但平台在调用前后还要管凭据：

```python
def checkin(ctx, payload):
    """自助签到：学号与姓名都对上才记一次到场。返回给报名人看的信息。"""
```

## actions：写操作的声明

平台按声明生成 `/<token>/api/do/<name>`，并按声明做鉴权、事务、审计与版本号。

```json
"actions": {
  "mark": {
    "title": "标记完成",
    "args": {"student": {"type": "int", "required": true},
             "kind": {"type": "str", "required": true, "max": 16}},
    "seat": "member",
    "unlock": true,
    "finalized": "block",
    "target": "student:{student}"
  },
  "draw": {"title": "抽一次", "args": {"prize": {"type": "int", "required": true}},
           "seat": "owner", "unlock": true, "target": "draw"}
}
```

| 键 | 默认 | 意思 |
| --- | --- | --- |
| `title` | 动作名 | 审计里怎么写 |
| `args` | `{}` | 参数表：`type`（int / str / bool / float / json）、`required`、`max` |
| `seat` | `member` | 要什么席位：`member` 或 `owner` |
| `unlock` | `false` | 要不要先打开「解锁修改」开关（现场高危动作一律走这个，现场没时间输密码） |
| `finalized` | `block` | 活动已结算时是否还允许：`block` / `allow` |
| `target` | `event` | 版本号对象模板，`{arg}` 会替换成参数；并发冲突与实时同步都按它算 |

平台在调用前后做的固定几件事：解析账号与席位 → 校验 `unlock` / `finalized` / 参数 →
开事务 → 调 `action()` → 冲突检查 → 记审计（`title` + 模块返回的 `audit`）→ 推版本号。

## 页面与资源

模块的页签由平台画外壳，内容由模块给：

```
pages/ops.html          这个页签的内容（一段 HTML，不含 <html> 外壳）
pages/ops.js            这个页签的脚本，平台在页面切换时载入一次
pages/ops.css           可选
```

平台注入时提供两样东西：

- `LT`：`esc` / `toast` / `modal` / `confirm` / `api` / `panelApi` / `fmtTime` 这些现成件
- `page`：`{ id, event, account, seat, refresh() }`；`refresh()` 重新拉一次 `state`

模块脚本的入口是 `window.MODULE.pages.ops = { mount(page), unmount() }`。
不提供 `unmount` 就什么也不做。

## 数据

活动库归平台建：平台先建自己的表（`config` / `audit`），再执行模块的 `schema.sql`。

- `schema.sql` 必须可重复执行（`CREATE TABLE IF NOT EXISTS`）
- 表名以一个短前缀开头，避免与平台表撞名（例如 `lottery_students`）
- 改表结构：`module.json` 里把 `"schema": 2` 往上加，同时放 `migrations/2.sql`；
  平台按活动库里记的版本依次执行。不写迁移、只改 `schema.sql` 的改动只在**新建活动**上生效

## 展示数据

公示页与大屏的公共字段由平台管（活动名、状态、签到人数这种壳子上的东西），模块给两样：

```python
def present(conn, cfg):
    """公示页/大屏要展示的东西：{"counters": [{"label": "已发奖票", "value": 42}],
       "list": {"title": "中奖名单", "rows": [{"name": "高勇", "note": "三等奖"}]}}"""
```

`board` 里声明要哪几块；模块返回哪块就显示哪块。

## 错误、审计、并发

- 出错抛 `errors.ApiError("一句话", 状态码)`；平台翻成 `{ok: false, error}`，
  没接住的异常一律 500 并且不把栈写给用户
- 审计由平台记：动作跑完，平台写一条「谁、什么时候、什么动作、说明」。
  模块想补充细节就在返回值里给 `"audit": "…"`
- 并发冲突按动作声明的 `target` 算：同一个对象被别人先改过就返回 409，
  客户端提示「另一个会话刚刚改过这一项」
- 模块不要自己开关事务、不要自己推版本号，那是平台的事

## 一个最小模块

```
modules/hello/
  module.json      {"id": "nwuca.hello", "name": "打招呼", "version": "0.1.0", "api": 1,
                    "pages": [{"id": "main", "title": "主页"}], "external": ["board"],
                    "actions": {"say": {"title": "说一句", "args": {"text": {"type": "str"}}}}}
  schema.sql       -- 自己那几张表
  defaults.json    {}      -- 这个玩法的默认配置键
  pages/main.html  <h2>打招呼</h2><button id="say">说一句</button>
  pages/main.js    window.MODULE.pages.main = { mount(page) { … } }
  model.py         def action(name, ctx, payload): return {"said": payload["text"]}
  doc.md           现场怎么用
```

## 迁移路线

现在的样子是「一个模块的所有玩法逻辑都在平台里长着」。按下面几步搬，每步都要套件全绿：

1. **（已完成）** 业务模型搬进模块，核心只留分发器
2. 动作声明：`module.json` 的 `actions` + 平台侧统一的 `/api/do/<name>` 调度；
   抽奖模块先把自己的动作声明出来，平台里那 20 多个 `/api/admin/*` 收成调度与少数平台动作
3. 页面资源：平台按 `pages/*.html|js` 注入，`tpl/admin.html` 里与奖票相关的段落搬进模块
4. 展示数据：公示页/大屏改吃 `present()`，平台只画壳
5. 验收：装两个玩法完全不同的模块同时跑；`git grep` 一遍平台代码里没有玩法名词

第 2 步与第 3 步之间不要交叉做：先把接口形状稳定下来，再搬页面。

## 相关文档

- [模块开发指南](modules.md)
- [结构](architecture.md)
- [界面规范](ui-spec.md)
