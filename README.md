# CAEventManager

一场学生社团活动在现场要用到的东西：签到、任务进度、奖票发放、抽奖，以及每一次操作的留痕。自托管、
单进程，Python 3 标准库足够，没有第三方运行时依赖；前端是原生 JS，没有构建步骤。同一个进程服务主面板
（账号、活动列表、审批）与每个活动的 token 段（公示页、大屏、签到、控制台），新增活动不改变路径结构。

## 路由

```
/                  主面板登录页
/register          主面板注册（提交后待审核）
/login             主面板登录（与 / 同一页）
/panel             主面板控制台
/panel_static/*    主面板静态资源
/api/panel/*       主面板接口

/<token>/          活动公示页，只读，无鉴权
/<token>/screen    大屏投影页
/<token>/checkin   自助签到，须带 ?t= 时效 token
/<token>/admin     活动控制台
/<token>/login     活动登录
/s/<code>          限时短码跳转，由控制台生成
```

路由与外部前缀无关：路径里出现已知活动 token 时按活动处理，否则把剩余路径按主面板路由匹配，因此
本机直连、反向代理前缀、将来的子域名都无需改代码。`public_base_prefix` 只用于生成绝对链接
（二维码、分享链接、Cookie 路径），不参与路由判定。

活动有两个标识：数字 `seq` 只用于存储，不出现在 URL 里；`token` 是 32 位 base64url 的访问凭据，
本身就是 URL 中的一段。`path_secret` 是首个活动的 token，可在控制台重新生成，旧链接随即失效。

## 活动页面

公示页只读，展示签到人数、任务完成数、已发奖票与中奖名单。大屏页在签到阶段显示签到二维码，抽奖阶段
改为活动地址与常驻中奖名单。签到页必须扫大屏二维码进入：token 按 10 秒窗口切片，扫码通过后手机得到
签名 presence cookie，再填学号与姓名，两项都与名单匹配才算签到成功。控制台页面栏由活动模块声明
（操作台、名单、奖票、抽奖），平台固定追加活动设置与日志。

## 账号与权限

账号分三级：站长（唯一）> 超级管理员 > 管理员。注册一律先落到「管理员 + 待审核」，审核通过才能登录。
用户管理、活动管理、审批只对超级管理员及以上可见，服务器管理与审计日志只对站长可见，接口层同样校验。
撤销、改时间、删人、作废抽奖、重置奖票、重新生成访问 id 需要先打开「解锁修改」，30 秒无操作自动上锁。

## 奖票池

| 模式 | 取值 | 说明 |
|---|---|---|
| 自动 | `到场人数 × 3 + 常数`（常数默认 20） | 跟着真实到场人数走，保底票永远发得出 |
| 固定 | 指定张数 | 印了确定数量实物票时使用 |
| 表达式 | 例如 `signed_in * 3 + 20` | 每次状态变化重新求值，按现场形状伸缩 |

每张票按顺序发号，与池子大小解耦，因此池子可以中途上调，也可以下调到不低于已发张数。表达式由小型
AST 求值器执行，只允许数字、上下文名字、四则运算与 `min` `max` `round` `abs` `int`，没有 `eval`；
可用名字有 `signed_in`、`present`、`participants`、`staff_present`、`tasks_done`、`guaranteed`、
`bonus_constant`、`pool_size`。

## 名单与导出

名单支持 CSV 与 TSV 导入，有表头自动识别，无表头按「学号,姓名」解析，按学号增量更新，重复导入不留
重复记录。姓名拼音由内置拼音表生成，检索同时匹配姓名、全拼、首字母与学号片段。所有改变状态的动作写入
审计日志；活动结束后从名单页导出 ZIP：`01-汇总`、`02-名单`、`03-奖票明细`、`04-抽奖记录`、
`05-中奖汇总` 五张 CSV 与 `06-操作日志.log`。

## 活动模块与模板

`modules/<id>/` 下放 `module.json`（标识、名称、版本、兼容约束、页面清单）与 `schema.json`（创建时定下的
参数）。创建活动的表单由 schema 生成，取值校验通过后写进该活动自己的库；兼容约束按本版本号判定，不满足
的模块不出现在创建列表里。`templates/<id>.json` 是模块之上的一种安排：选定模块并预填参数或默认活动名，
只能设置模块已声明的参数，写别的键会被拒绝。

## 部署

目标是一台能 ssh 的 Linux 主机，只需要 python3 与 tar。

```powershell
.\deploy.ps1 -Remote user@host              # 上传并重启
.\deploy.ps1 -Remote user@host -NoRestart   # 只同步，远端目录默认 ~/apps/eventmanager
```

脚本归一化行尾后打包源码，用 base64 经 ssh stdin 送出（目标机没有 sftp），远端解包，逐个确认文件落地
并试 import 主模块，全部通过才重启；远端的 `data/` 与 `config.json` 始终不动。

```sh
cd ~/apps/eventmanager
./service.sh status            # 打印公示 / 大屏 / 控制台三个地址，活动前先跑一次
./service.sh start|stop|restart
./service.sh log 50            # 另有 check / reset
```

服务不会自动拉起。挂载在 `https://example.org/mount` 时，活动页形如 `https://example.org/mount/<token>/`，
大屏、签到、控制台是同前缀下的 `screen`、`checkin`、`admin`。部署后可用 `tools/` 下的脚本自检：
`unit_test.py` 无需服务，`panel_test.py` 打主面板接口，`smoke_test.py` 打活动接口，
`panel_ui_test.py` 与 `ui_test.py` 用 Playwright 驱动真实页面。

## 配置

`config.json` 位于仓库根目录，首次启动时自动写入。

| 键 | 默认值 | 作用 |
|---|---|---|
| `path_secret` | 自动生成 | 首个活动的访问 token，也是它的 URL 段 |
| `host` | `0.0.0.0` | 监听地址 |
| `port` | `8931` | 监听端口 |
| `public_base_prefix` | `http://127.0.0.1:8931` | 对外访问前缀，只用于拼绝对链接 |
| `owner_username` | `owner` | 站长登录名 |
| `owner_name` | `站长` | 站长显示名，界面上只出现这个 |

活动标题、奖票池、奖品、大屏阶段、导出时区等属于单个活动，存在该活动自己的库里，不进 `config.json`。
环境变量可覆盖：`LOTTERY_CONFIG`、`LOTTERY_DATA_DIR`、`LOTTERY_HOST`、`LOTTERY_PORT`。数据目录中，
`data/panel.sqlite3` 存账号、活动元数据、成员与审计，`data/events/<seq>.sqlite3` 是每个活动自己的库，
`data/avatars/` 存头像，日志在 `data/service.log`；首次启动生成的两个密码在 `data/owner_password.txt`
与 `data/admin_password.txt`。

Powered with ❤ by Tianming & Deepseek
