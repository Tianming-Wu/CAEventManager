# 服务有效性接口 - 查询服务状态提示

以下只读接口返回当前服务状态提示。

```
GET /api/panel/service     面板侧（需要登录）
GET /<token>/api/service   活动侧（公开）
```

两条接口采用相同的响应格式。

```json
{"ok": true, "version": "1.7.0.33", "at": 1762778520.1,
 "items": [{"id": "svc:disk", "kind": "service", "level": "important",
            "title": "数据盘空间不足", "body": "只剩 4.2 GB（8%）。…",
            "sticky": true}]}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定标识（`svc:cpu` / `svc:mem` / `svc:disk`），用于关联「不再提示」设置 |
| `level` | 通知级别，可为 `info`、`important` 或 `critical` |
| `title` / `body` | 提示标题与包含当前读数的正文 |
| `sticky` | 是否为持续提示。`false` 表示条件恢复后自动消失，`true` 表示需要处理 |

响应反映请求时的服务状态。条件消失后，后续响应不再包含该提示。「不再提示」设置保存在浏览器中。

判定阈值为 CPU 占用 90%、内存占用 92%、磁盘剩余 12% 或 500 MB。磁盘空间即将用尽的判定阈值为剩余 3% 或 100 MB。

## 相关文档

- [接口](api.md)
- [通知与服务提示](../usage/notices.md)
