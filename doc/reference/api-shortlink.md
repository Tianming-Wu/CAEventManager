# 短链接口 - 生成、列出、删除

短链管理接口位于面板 API。活动成员可管理所属活动的短链，超级管理员及站长可按权限回收短链。
模块须在 `module.json` 声明 `features.shortlink`，对应活动才可生成短链，
默认目标限定在活动范围内，声明相应高危权限的模块可使用外部目标。

```
POST   /api/panel/shortlink          {seq, target, minutes, max_hits}   生成
GET    /api/panel/shortlink?seq=…    本活动的短链列表（含已失效的）与可选目标
POST   /api/panel/shortlink/delete   {code}                            立即删除
GET    /api/panel/shortlink/overview 超级管理员：每个活动还持有多少条
```

公开短链跳转无需登录：

```
GET /s/<code>        命中 302 到「前缀 + 活动 token + 目标路径」；不存在 / 过期 / 用尽 / 已删 / 活动已归档 都是 404
```

## 条目

```json
{"code": "AB3D7KM", "eventSeq": 3, "target": "screen", "createdBy": "example_user",
 "createdAt": 1794332130.1, "expiresAt": 1794333930.1, "maxHits": 2, "hits": 1,
 "revoked": false, "expired": false, "exhausted": false, "valid": true}
```

- `code`：7 位大写字母与数字，字符集排除 `0 O 1 I` 这类易混字符；**查找不区分大小写**
- `target`：活动内的相对路径，空串表示公示页。模块可指向活动范围内的任意路径
- `expiresAt` / `maxHits` 为 0 表示不设限
- `valid` 表示查询时的有效状态。短链删除、到期或达到访问上限后会失效

## 请求限制

| 情况 | 结果 |
| --- | --- |
| 不是这个活动的成员 | 403 |
| 活动用的模块没有申请 `features.shortlink` | 403 |
| 常规短链的目标为绝对地址、含 `..` 或指向 `/s/` | 400 |
| 有效期超过 30 天，或次数写成负数 | 400 |
| 已达静态设置里的条数上限（默认 5） | 400，并说明当前条数 |
| 代其他活动删除短链 | 权限不足返回 403。超级管理员及站长须提权 |

活动归档时，平台会回收该活动的短链。

## 相关文档

- [短链的使用](../usage/shortlinks.md)
- [接口总览](api.md)
