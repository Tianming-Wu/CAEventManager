# 接口 - 路由分组、鉴权、事件流与错误约定

面板与外部程序使用下列接口。操作受账号权限限制，并按平台规则记录审计日志。

## 路由分组

```
/                  主面板登录页（/login 是同一页）
/register          主面板注册
/panel             主面板控制台
/print             打印页：名单 / 报名单（?kind=roster|signup&id=，可选 mode=blank）
/assets/*          面板与活动共用的静态资源
/api/panel/*       面板接口

/<token>/login     转至面板登录，登录后返回活动后台
/<token>/api/*     活动接口
/s/<code>          已生成的短链跳转入口
```

路径中的活动访问凭据用于确定活动。部署在子路径时，在上述路径前加上挂载前缀。配置项 `public_base_prefix` 用于生成对外链接，路由按请求路径匹配。

活动页面的可用性由模块声明及活动设置决定。可用页面参见相应模块的说明。只有支持短链的活动可生成短链，参见[短链接口](api-shortlink.md)。

## 浏览器侧的鉴权

- 登录后获得会话 Cookie（`HttpOnly`，路径限定在挂载前缀下）
- 使用浏览器会话调用要求跨站请求校验的写入接口时，发送 `X-Requested-With: CAEventManager`
- 需要提权时返回 HTTP 403 及 `sudoRequired`。客户端完成密码认证后可重发请求
- JSON 响应中的 `version` 表示服务版本，`revs` 表示对象版本。客户端据此处理刷新及并发写入

外部程序的鉴权方式见 [API 令牌](api-tokens.md)。

## 活动数据的归档与下载

```
POST /api/panel/event/archive        负责人归档自己的活动
POST /api/panel/event/force_archive  超级管理员强制归档活动（须提权）
POST /api/panel/event/unarchive      超级管理员取消归档（须提权）
GET  /api/panel/event/download?seq=  下载活动数据，权限按归档状态判定
POST /api/panel/event/delete         超级管理员彻底删除已归档的活动（须提权）
```

下载返回 ZIP 文件。不同状态下的操作权限及入口参见[活动管理](../usage/platform-pages.md#活动管理)。

## 事件流

- 面板：`GET /api/panel/stream`（需要登录）
- 活动：`GET /<token>/api/stream`（不需要登录，只收本活动的变化）

服务端通过事件流推送相关对象的版本变化。客户端可据此重新请求所需数据。

## 错误约定

| 状态 | 含义 | 界面处理 |
| --- | --- | --- |
| 401 | 未登录或会话过期 | 回登录页，登录后回到原页面 |
| 403 | 权限不足或需要提权 | 显示原因，按响应提示请求密码认证 |
| 409 | 对象状态或版本冲突 | 提示「另一个会话刚刚改过这一项」，以服务端状态为准 |
| 413 | 请求体过大（例如存档配额满了） | 说明并提示清理 |
| 502 | 外部端点失败（模型接口） | 说明是调用模型失败 |

JSON 错误响应包含 `ok: false` 和面向使用者的 `error` 文本。

## 相关文档

- [短链接口](api-shortlink.md)
- [API 令牌](api-tokens.md)
- [表格导入导出接口](api-tables.md)
- [服务有效性接口](api-service.md)
