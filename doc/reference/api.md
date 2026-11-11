# 接口 - 路由家族、鉴权、事件流与错误约定

面板自己的前端就用这套接口，所以它们不是"内部接口"再包一层——同一个进程、同一套权限、同一份审计。

## 路由家族

```
/                  主面板登录页（/login 是同一页）
/register          主面板注册
/panel             主面板控制台
/print             打印页：名单 / 报名单（?kind=roster|signup&id=，可选 mode=blank）
/panel_static/*    面板静态资源
/api/panel/*       面板接口

/<token>/          活动公示页（只读，无鉴权）
/<token>/screen    大屏
/<token>/checkin   自助签到（须带 ?t= 时效 token）
/<token>/admin     活动后台（用面板账号登录）
/<token>/login     跳面板登录，登录后回到该活动后台
/<token>/api/*     活动接口
/s/<code>          短链跳转（平台级的转发表，见 api-shortlink.md）
```

路径里出现已知活动 token 时按活动处理，否则把剩余路径按面板路由匹配——所以本机直连、
反向代理前缀、将来的子域名都不用改代码。`config.json` 里的 `public_base_prefix` 只用于生成
绝对链接（二维码、分享链接、Cookie 路径），不参与路由判定。

## 浏览器侧的鉴权

- 登录后拿到会话 cookie（`HttpOnly`，路径限定在挂载前缀下）
- 所有写请求要带 `X-Requested-With: CAEventManager`——跨站表单设不了自定义头，这是 CSRF 防线
- 敏感操作返回 `403` + `sudoRequired`，界面拦一次密码后再重发同一个请求
- 每个回答都带 `version`（服务版本）与 `revs`（各对象的版本号），页面据此判断要不要刷新、
  以及写入有没有冲突

平台之外的程序用 API 令牌，见 [api-tokens.md](api-tokens.md)。

## 活动数据的归档与下载

```
POST /api/panel/event/archive        负责人归档自己的活动（要 sudo）
POST /api/panel/event/force_archive  超管强制归档别人的活动（要 sudo）
POST /api/panel/event/unarchive      超管取消归档（要 sudo）
GET  /api/panel/event/download?seq=  下载：开启时负责人与参与者；已归档时超管 + sudo（含归档包）
POST /api/panel/event/delete         超管彻底删除已归档的活动（要 sudo）
```

归档会把 `data/events/<活动号>/` 打包成 `archive.tar.gz`，删掉业务库与审计库，只留元数据；
取消归档把包解开。下载给的是 zip，文件名里带活动号。

## 活动级提权

活动内的高危动作（模块在 `actions` 里写 `"sudo": true`）要单独提一次权，和主站 sudo 互不代替：

```
POST /<token>/api/sudo        {password}   提权 30 秒，只对「这个会话 × 这个活动」
POST /<token>/api/sudo/exit                立即退出
```

没提权时接口回 `403` + `sudoRequired`，客户端应当弹一次密码框、提权之后重发**同一个**请求。
换活动、换设备都要重新输；30 秒一过就失效。

## 事件流

- 面板：`GET /api/panel/stream`（需要登录）
- 活动：`GET /<token>/api/stream`（不需要登录，只收本活动的变化）

服务端在每次成功写入之后把相关对象的版本号加一，通过事件流推送。客户端收到后重新读当前页面；
连不上时退回轮询（大屏 1.5 秒、公示页 5 秒），连上之后轮询降为半分钟一次的对账读取。
公示页的读取带 `ETag`，没有变化时回 304。

## 错误约定

| 状态 | 含义 | 界面处理 |
| --- | --- | --- |
| 401 | 未登录或会话过期 | 回登录页，登录后回到原页面 |
| 403 | 没有这个权限（或需要重新输密码） | 说明原因；`sudo` 时拦一次密码 |
| 409 | 同一对象被别人先改过 | 提示「另一个会话刚刚改过这一项」，以服务端状态为准 |
| 413 | 请求体过大（例如存档配额满了） | 说明并提示清理 |
| 502 | 外部端点失败（模型接口） | 说明是调用模型失败 |

错误体统一是 `{ok: false, error: "给用户看的一句话"}`。

## 相关文档

- [短链接口](api-shortlink.md)
- [API 令牌](api-tokens.md)
- [表格导入导出接口](api-tables.md)
- [服务有效性接口](api-service.md)
