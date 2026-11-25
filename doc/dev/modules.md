# 模块开发指南 - 组织文件、声明能力与验证

活动可用的业务逻辑、设置及页面由模块提供。字段、函数和前端回调的完整约定见[模块接口](module-interface.md)。

## 组织模块

内置模块位于 `builtin/modules/<id>/`，实例安装的模块位于 `modules/<id>/`。

```text
module.json          模块标识、版本、页面与能力声明
schema.json          创建活动时使用的参数
model.py             业务逻辑
schema.sql           活动数据库表定义
defaults.json        模块设置的默认值
pages/               活动后台页签及资源
external/            模块提供的活动页面及资源
templates/           随模块提供的预设
doc.md               模块信息及使用说明
```

先在 `module.json` 中填写唯一模块 ID、显示名称、版本及适用的平台版本。按实际需要声明 `pages`、`external`、`actions` 和 `features`。在 `doc.md` 中记录模块 ID 和可用页面。预设只能设置 `schema.json` 已声明的参数。

## 能力声明

| 能力 | 平台提供的功能 |
| --- | --- |
| `roster` | 从活动设置绑定平台名单，并按学号同步成员 |
| `search` | 依据模块声明的字段、拼音及标签匹配人员 |
| `shortlink` | 在活动设置提供短链管理，并按活动归属、限额及回收 |
| `super.anyshortlink` | 允许短链指向活动地址范围外的目标 |

能力定义见 `core/modules.py` 的 `CAPABILITIES`。解析时会忽略无效声明并记录原因。短链目标、接口及限制参见[短链接口](../reference/api-shortlink.md)。高危权限的导入提示及生效规则见[模块接口](module-interface.md#高危权限)。

### 搜索

将下列对象作为 `module.json` 的 `features` 字段值。

```json
{
  "search": {
    "records": "students",
    "name": "name",
    "key": "sid",
    "fields": ["grp", "note"],
    "tags": [
      {"keys": ["w", "工作人员"], "label": "#w 工作人员", "field": "role", "eq": "staff"},
      {"keys": ["未签到"], "label": "#未签到", "field": "marks.checkin", "filled": false}
    ]
  }
}
```

`records` 指定状态中的记录集合。`name` 用于构建姓名拼音索引，`key` 和 `fields` 指定其他可匹配字段，`tags` 映射标签词和记录条件。拼音音节匹配及组合搜索由 `assets/js/event.js` 提供。

## 打包与验证

按[打包与安装](module-interface.md#打包与安装)的约定生成 ZIP 包。安装后，创建使用该模块的活动，检查创建参数、页签、活动页面和导出结果。声明了高危权限时，同时检查导入提示。

```powershell
python tools/run_suites.py unit modules
```

可参考内置「奖票抽奖」的搜索、动作及模块页面，以及「讲座」的议程、提问和展示数据。

## 相关文档

- [模块接口](module-interface.md)
- [奖票抽奖](../../builtin/modules/nwuca.lottery/doc.md)
- [讲座](../../builtin/modules/nwuca.talk/doc.md)
- [项目架构](architecture.md)
