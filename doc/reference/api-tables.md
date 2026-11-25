# 表格导入导出接口 - 名单与报名的 CSV 与 xlsx

名单与报名表格通过面板接口导入和导出，适用各对象的访问与写入权限。

```
GET  /api/panel/roster/export.csv?id=<名单>        GET  /api/panel/roster/export.xlsx?id=<名单>
GET  /api/panel/signup/export.csv?id=<报名>        GET  /api/panel/signup/export.xlsx?id=<报名>
POST /api/panel/roster/import.xlsx?id=<名单>        （请求体为 xlsx 文件字节）
POST /api/panel/roster/import.xlsx?name=<新名单>    （省略 id 时创建新名单）
```

- 导出文件仅包含表头与数据行
- CSV 包含 BOM。xlsx 使用文本单元格，保留学号的前导零
- xlsx 导入支持内联字符串、共享字符串、数字和布尔值。读取首个工作表，跳过空行，合并单元格仅取左上角的值。公式和图表不在支持范围内
- xlsx 导入请求体上限为 4 MB。文件格式不符时，接口返回错误

## 相关文档

- [接口](api.md)
- [导出与打印](../usage/output.md)
