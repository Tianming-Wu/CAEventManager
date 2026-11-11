# 部署与运维 - 上传、版本号、日志与备份

目标是一台能 ssh 的 Linux 主机，只需要 `python3` 与 `tar`。没有数据库服务、没有 Node、没有构建步骤。

## 部署

```powershell
.\bin\deploy.ps1 -Remote user@host                  # 上传并重启
.\bin\deploy.ps1 -Remote user@host -NoRestart       # 只同步
.\bin\deploy.ps1 -Remote user@host -Dest apps/lottery
```

脚本做的事：归一化行尾 → 打包源码 → 用 `cmd /c type` 把 tarball 经 ssh stdin 送出（目标机没有
sftp）→ 远端解包 → 逐个确认文件落地并试 import 主模块 → 全部通过才重启。

**远端的 `data/` 与 `config.json` 始终不动**，所以对话存档、名单、活动、账号都跟着机器走，
不跟着代码走。

## 服务脚本

```sh
cd ~/apps/lottery
./bin/service.sh status            # 打印公示 / 大屏 / 控制台三个地址，活动前先执行一次
./bin/service.sh start|stop|restart
./bin/service.sh log 50            # 另外还有 check / reset
```

`bin/run.sh` 是前台运行器：用 `flock` 保证只有一个实例，`python3 -u app.py` 的输出追加到
`data/service.log`。服务**不会自动拉起**，重启机器之后要手动 `start`（或交给 systemd 之类的守护）。

## 版本号

四段 `主.次.修.构`，在 `app.py` 的 `VERSION`：

| 段 | 什么时候动 |
| --- | --- |
| 主 | 架构或兼容性变了（老模块、老预设、老数据库要跟着动） |
| 次 | 一批新功能落地 |
| 修 | 行为变化，或一批修复 |
| 构 | **只增不减**：推进任何一段它都跟着 +1。所以它是"这台机器的第几次发布" |

```powershell
python tools/bump_version.py            # 只推进「构」
python tools/bump_version.py patch      # 「修」+1，「构」也 +1
python tools/bump_version.py --show
```

每次上线都要推进：页面靠版本号判断"服务换了版本"，没变就不会换新脚本。

## 重新部署之后

服务重启不会把人踢下线，但已经开着的页面仍在执行旧脚本。所以每个回答都带着版本号：

- 面板：这一页没敲过字、没有开着的弹窗就直接刷新；否则在内容上方出一条「服务已更新到 vX」，
  点「刷新」再换。填了一半的表单不会被吞掉
- 大屏：抽奖动画播完再刷新
- 公示页：直接换

## 安全模式

出配置或数据问题时，把 `config.json` 里的 `safe_mode` 改成 `true` 再重启，服务进入安全模式：

- 外部资源一个都不加载，内置模块也算；活动页与活动接口一律不提供（返回 503 与一句说明），
  数据原样留在 `data/` 里，不做兜底改写
- **平台自己的东西照常可改**：账号、角色、模块启用状态、短链、通知、配置
- 只有站长能登录，其他账号会看到 `Service is under maintenance`
- 顶栏通知中心里常挂一条「服务处于安全模式」，写明怎么退出

退出：把开关改回 `false` 再重启。

## 日志

`data/service.log`。写到 8 MB 自己换一份：改名成 `.1`，旧的依次往后挪，最多留三份（`.1`~`.3`）。
服务是 `>>data/service.log` 起的，所以轮转是把同名文件重新开一份挂到 fd 1 上；日志写不出去不会
让请求失败。`LOTTERY_LOG_MAX` 改上限（字节），`0` 关掉轮转。

## 备份与还原

需要备份的是整个 `data/` 目录，其中：

| 内容 | 说明 |
| --- | --- |
| `panel.sqlite3` | 账号、活动索引、名单与报名索引、令牌、助手存档 |
| `lists.sqlite3` | 名单与人员库 |
| `events/<活动号>/data.sqlite3` | 这个活动的业务库 |
| `events/<活动号>/audit.sqlite3` | 这个活动的审计日志，只追加 |
| `events/<活动号>/meta.json` | 活动名、开始时间、用的模块 |
| `owner_password.txt` | 首次启动生成的站长密码 |
| `avatars/` | 头像 |

活动**没有删除操作**，只有存档：存档之后数据库文件不动，随时可以查。所以备份就是复制 `data/`。

一个活动就是一个文件夹：`data/events/<活动号>/` 里的东西自足，把一个文件夹拷走就能单独还原那一场。

还原：停服务 → 用备份覆盖 `data/` → `./bin/service.sh start`。注意 SQLite 的 `-wal` 与 `-shm`
要和主库一起复制，或者在停服务之后复制（那时 WAL 已经落盘）。

`./bin/service.sh reset` 会清空活动数据（活动与账号都重建），重置前会要求输入 `yes`，并自动把旧库
备份成 `data/backup-<时间>-*`。新的站长密码重新写进 `data/owner_password.txt`。

## 自检

```sh
python tools/panel_test.py http://127.0.0.1:8931     # 面板接口
python tools/smoke_test.py http://127.0.0.1:8931     # 活动接口与事件流
python tools/prod_check.py https://example.org/mount <活动 token>   # 线上巡检
```

`unit_test.py` 不需要服务；`panel_ui_test.py` 与 `ui_test.py` 用 Playwright 驱动真实页面，
需要本机有 Edge。开发时用 `python tools/run_suites.py unit panel_test panel_ui_test smoke ui`
一次执行完毕（自带空闲端口与临时数据目录）。

## 配置

`config.json` 位于应用根目录，首次启动时自动写入：

| 键 | 作用 |
| --- | --- |
| `port` | 监听端口 |
| `host` | 监听地址 |
| `public_base_prefix` | 生成绝对链接用的前缀，不参与路由 |
| `owner_username` / `owner_name` | 站长的登录名与称呼 |
| `shortlink_per_event` | 静态设置：一个活动最多同时持有几条短链（默认 5），改完重启生效 |
| `safe_mode` | 静态设置：服务安全模式（默认 `false`），改完重启生效 |

随机源：有 `/dev/hwrng` 就与内核的混合着用，没有就只用内核的 CSPRNG。想知道这台机器用的是哪一种，
看服务启动后在服务器上执行 `python3 -c "import sys; sys.path.insert(0,'core'); import rng; print(rng.source())"`。

环境变量可以覆盖：`LOTTERY_DATA_DIR`、`LOTTERY_PACKS_DIR`、`LOTTERY_PORT`、
`LOTTERY_VERSION`（测试用）、`LOTTERY_LOG_MAX`、`LOTTERY_ASSISTANT_PROMPT`。

## 相关文档

- [接口](../reference/api.md)
- [结构](../dev/architecture.md)
