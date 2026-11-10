# CAEventManager - 学生社团活动管理服务平台

学生社团活动管理服务平台，提供活动报名、现场签到、抽奖等功能。提供审计级别日志和软删除。

一套服务同时承载主面板与多个活动：主面板管理账号、名单与报名，每个活动各有公示页、大屏、
签到与活动后台。自托管，单进程运行。

## 环境要求

- Python 3
- 部署主机可通过 ssh 访问，并存在 tar 命令
- 运行界面测试需要本机安装 Microsoft Edge 与 Playwright

## 安装与启动

```sh
python3 app.py
```

首次启动会生成默认配置（`config.json`），并生成默认站长密码写入 `data/owner_password.txt`。
服务启动后，使用浏览器访问 `http://127.0.0.1:8931/` 并登录。

## 部署

```powershell
.\bin\deploy.ps1 -Remote user@host -Dest apps/lottery
```

## 配置

`config.json` 在应用根目录，首次启动时自动写入：

| 键 | 默认值 | 作用 |
| --- | --- | --- |
| `host` | `0.0.0.0` | 监听地址 |
| `port` | `8931` | 监听端口 |
| `path_secret` | 自动生成 | 首个活动的访问凭据，也就是地址里的那一段 |
| `public_base_prefix` | `http://127.0.0.1:8931` | 拼绝对链接用，不参与路由判定 |
| `owner_username` / `owner_name` | `owner` / `站长` | 站长的登录名与称呼 |

环境变量：`LOTTERY_DATA_DIR`、`LOTTERY_PACKS_DIR`、`LOTTERY_HOST`、`LOTTERY_PORT`、
`LOTTERY_VERSION`、`LOTTERY_LOG_MAX`、`LOTTERY_ASSISTANT_PROMPT`。

## 文档

| 分类 | 位置 |
| --- | --- |
| 使用说明 | [doc/usage/](doc/usage/) |
| 参考 | [doc/reference/](doc/reference/) |
| 部署与运维 | [doc/ops/deploy.md](doc/ops/deploy.md) |
| 开发 | [doc/dev/](doc/dev/) |
| 计划与取舍 | [ROADMAP.md](ROADMAP.md) |
| 全部文档 | [doc/README.md](doc/README.md) |

## 注意事项

- 活动没有删除操作，只有存档；存档后数据保留，可随时查阅
- 影响权限与数据的敏感操作需要重新输入一次密码
- 运维助手为只读，修改数据回到界面完成

## 限制与路线图

- 不做多租户：一套服务属于一个组织
- 不引入第三方运行时依赖
- 不给运维助手开放写入权限

计划与取舍见 [ROADMAP.md](ROADMAP.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。
