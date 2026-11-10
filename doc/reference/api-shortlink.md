# 短链接口 - 生成、列出、删除

短链是平台自己的一张转发表，接口在面板族：活动成员管自己活动的短链，超级管理员能删任何活动的。
模块调用前要先在 `module.json` 里申请 `features.shortlink`；申请之后，它负责的活动就能生成短链，
指向活动范围内的任意路径（见 [模块开发指南](../dev/modules.md)）。

```
POST   /api/panel/shortlink          {seq, target, minutes, max_hits}   生成
GET    /api/panel/shortlink?seq=…    本活动的短链列表（含已失效的）与几个现成目标候选
POST   /api/panel/shortlink/delete   {code}                            立即删除
GET    /api/panel/shortlink/overview 超级管理员：每个活动还持有多少条
```

公开的转发表在面板族之外，不需要登录：

```
GET /s/<code>        命中 302 到「前缀 + 活动 token + 目标路径」；不存在 / 过期 / 用尽 / 已删 / 活动已归档 都是 404
```

## 条目

```json
{"code": "AB3D7KM", "eventSeq": 3, "target": "screen", "createdBy": "laffey",
 "createdAt": 1794332130.1, "expiresAt": 1794333930.1, "maxHits": 2, "hits": 1,
 "revoked": false, "expired": false, "exhausted": false, "valid": true}
```

- `code`：7 位大写字母与数字，字母表去掉 `0 O 1 I` 这类易混字符；**查找不区分大小写**
- `target`：活动内的相对路径，空串表示公示页。模块可指向活动范围内的任意路径
- `expiresAt` / `maxHits` 为 0 表示不设限
- **`valid` 是调用方唯一该信的那一项**：短链随时可能被删、过期或用尽，用之前按它判断，
  不要缓存“它还能用”

## 拒绝的情况

| 情况 | 结果 |
| --- | --- |
| 不是这个活动的成员 | 403 |
| 活动用的模块没有申请 `features.shortlink` | 403 |
| 目标是带协议的绝对地址、含 `..`，或又指向 `/s/` | 400 |
| 有效期超过 30 天，或次数写成负数 | 400 |
| 已达静态设置里的条数上限（默认 5） | 400，并说明当前条数 |
| 删别人的短链又不是超级管理员 | 403；是超管则要求先进入 sudo 模式 |

活动归档时平台会把这些短链一并销毁，不需要调用方处理。

## 相关文档

- [短链的使用](../usage/shortlinks.md)
- [接口总览](api.md)
