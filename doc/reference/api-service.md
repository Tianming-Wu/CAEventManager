# 服务有效性接口

只发警告，不发指令：这一族接口读出来的都是"现在是什么样"，没有任何让它做什么的入口。

```
GET /api/panel/service     面板侧（需要登录）
GET /<token>/api/service   活动侧（公开，与 /api/board 同一档）
```

两边回的是同一份东西：

```json
{"ok": true, "version": "1.7.0.33", "at": 1762778520.1,
 "items": [{"id": "svc:disk", "kind": "service", "level": "important",
            "title": "数据盘空间不足", "body": "只剩 4.2 GB（8%）。…",
            "sticky": true}]}
```

| 字段 | 意思 |
| --- | --- |
| `id` | 稳定标识（`svc:cpu` / `svc:mem` / `svc:disk`），前端用它记住"不再提示" |
| `level` | 与通知同一档：`info` / `important` / `critical` |
| `title` / `body` | 给用户看的字，读数写在正文里 |
| `sticky` | 会不会自己消失。`false` 表示条件一好转就没了；`true` 表示要人处理 |

条目是**按当时的读数推出来的**，不落库：条件消失，下次请求里就没有这一条了。所以没有"确认"、
"忽略"之类的写接口——前端最多在本地记住用户点过「不再提示」。

判定阈值在 `health.py` 开头（CPU 90%、内存 92%、磁盘剩余 12% 或 500 MB；几乎用尽按 3% 或 100 MB），
那里是静态常量，改了重启生效。

界面上的样子见 [`../usage/notices.md`](../usage/notices.md)。
