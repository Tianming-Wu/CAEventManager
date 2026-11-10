# 模块开发指南 - module.json、能力声明、页面与屏幕

模块是活动的内容：装上并启用某个模块，才有它的页面、设置与现场玩法。平台只提供每个活动都有的那几页
（公示页、大屏、签到、活动设置、日志）。

## 目录与 `module.json`

```
builtin/modules/<id>/            随版本发布的模块
modules/<id>/                    实例自己装的模块（不进版本库）
  module.json                    标识、名称、版本、页面、能力声明
  schema.json                    创建活动时要填的参数（平台据此画表单）
  templates/*.json               随模块发布的预设
  doc.md                         这个模块的现场说明（运维助手也读这份）
```

```json
{
  "id": "nwuca.lottery",
  "name": "奖票抽奖",
  "version": "1.0.0",
  "requires": ">=1.4",
  "external": ["board", "screen", "checkin", "admin"],
  "pages": [{"id": "ops", "title": "操作台"}],
  "features": { "search": { } }
}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 唯一标识，形如 `nwuca.lottery`；模块之间的完全限定名就以它开头 |
| `requires` | 需要的平台版本，不满足就不出现在创建列表里 |
| `pages` | 申请哪些控制台页面（平台提供的那几种画法） |
| `external` | 需要哪些公开页面（公示页、大屏、签到） |
| `features` | **能力声明**：要用的平台功能必须在这里申请，见下 |

## 能力声明（`features`）

写法照安卓的 manifest：**名字 + 自己的定义**。跟玩法无关的东西（版本号热更新、事件流、导出打包）
平台一直给，不用申请；用得着的才声明，**没声明的模块在接口里根本看不到那一段**。

```json
"features": {
  "search": {
    "records": "students",       // state 里那份清单的键名
    "name": "name",              // 姓名字段：平台据此建拼音索引
    "key": "sid",                 // 学号/编号：片段匹配
    "fields": ["grp", "note"],    // 其余字段：只做"直接包含"
    "tags": [
      {"keys": ["w", "工作人员"], "label": "#w 工作人员", "field": "role", "eq": "staff"},
      {"keys": ["未签到"], "label": "#未签到", "field": "marks.checkin", "filled": false}
    ]
  }
}
```

- **平台出算法，模块出词表**：解析、拼音音节匹配、条件求交在 `static/common.js`；字段与标签词表是
  模块自己的事
- **没申请就不发**：没声明 `search` 的模块，`/api/admin/state` 里的记录连 `pys`（逐字音节）都不带
- **写错只丢那一项**：能力名认不出、声明类型不对，解析时丢掉并记原因，不影响模块其它部分
- 另一个能力是 `shortlink`：模块声明它的页面允许被短链指向哪些目标，平台负责生成、跳转与回收

```json
"features": {
  "shortlink": {
    "targets": [
      {"key": "screen", "label": "大屏"},
      {"key": "checkin", "label": "签到页"},
      {"key": "", "label": "公示页"}
    ]
  }
}
```

`key` 是活动内的相对路径（空串表示公示页），`label` 是界面上显示的名字。没申请这个能力的模块，
活动后台里没有短链入口，`/api/panel/shortlink` 也会拒绝。短链由平台保管、活动归档时销毁，
模块不要自己存短链号。

- 要加新能力，在 `modules.py` 的 `CAPABILITIES` 里加一项，再在 `FEATURE_DEFAULTS` 补默认值

## 模块之间的功能导出（计划中，尚未实现）

语法定在这里，实现见 ROADMAP：

```json
// 提供方 nwuca.random
"provides": {
  "real_random": {"kind": "js", "entry": "random.js", "export": "realRandom"}
}

// 使用方 nwuca.lottery
"depends": ["nwuca.random"],
"features": {
  "random": {"use": "nwuca.random.real_random"}
}
```

- 完全限定名是 `<模块 id>.<功能名>`，例如 `nwuca.random.real_random`
- **必须先声明 `depends`**：没声明就 `use` 别人的功能，这个模块直接判为无效、拒绝加载——
  让依赖关系只有一处可写，不靠"碰巧加载到了"
- 有了它就能写工具库型模块：只导出功能、不申请页面
- **现状**：模块只有 `requires`（平台版本约束）与预设的 `dependency`（预设依赖模块），
  还没有模块之间的依赖，所以这一条先搁置，等做依赖时一起实现（见 ROADMAP）

## 页面与屏幕

- 控制台页面由平台画（`pages` 里申请的那几种），模块只提供「显示什么数据、点击后调用哪个接口」
- 展示屏幕是可替换的插接件：`screen_packs/<标识>/screen.html`，活动配置里写 `screen_pack` 就切换
- 预设是模块之上的一份配置快照，只能设置模块 `schema.json` 声明过的参数

## 自测

```powershell
python tools/run_suites.py unit          # 模块清单与能力声明的解析都在这一套里
python tools/run_suites.py ui            # 起一个实例、灌演示数据、把控制台走一遍
```

## 相关文档

- [结构](architecture.md)
- [短链接口](../reference/api-shortlink.md)
- [奖票抽奖](../../builtin/modules/nwuca.lottery/doc.md)
