# 表格导入导出接口

名单与报名的表格进出，走面板自己的接口（同一套权限与会话）。

```
GET  /api/panel/roster/export.csv?id=<名单>        GET  /api/panel/roster/export.xlsx?id=<名单>
GET  /api/panel/signup/export.csv?id=<报名>        GET  /api/panel/signup/export.xlsx?id=<报名>
POST /api/panel/roster/import.xlsx?id=<名单>        （body 是 xlsx 的原始字节）
POST /api/panel/roster/import.xlsx?name=<新名单>    （没有 id 就用这份表格开一份新名单）
```

- **导出的表只有表头 + 数据行**：不加标题、不加说明、不加合计。第一行就是列名
- CSV 带 BOM，Excel 双击直接打开；xlsx 是文本单元格，学号不会变成数字、前导零不会掉
- xlsx 的读写认得 inlineStr / 共享字符串 / 数字 / 布尔，空行跳过，合并单元格只取左上角；
  公式、图表、多个工作表不处理（也不假装能处理）
- 导入上限 4 MB；不是 xlsx 的文件会被拒并说明原因

界面上的按钮怎么用见 [`../usage/output.md`](../usage/output.md)。
