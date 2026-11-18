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

**远端的 `data/`、`backups/` 与 `config.json` 始终不动**，所以对话存档、名单、活动、账号与备份包
都跟着机器走，不跟着代码走。部署会把「这次包里没有的旧文件」清掉（改过名、挪过目录的文件不会
留在服务器上），上面这三处、以及实例自己装的 `modules/`、`templates/`、`theme/` 都在豁免名单里。

## 服务脚本

```sh
cd ~/apps/lottery
./bin/service.sh status            # 打印公示 / 大屏 / 控制台三个地址，活动前先执行一次
./bin/service.sh start|stop|restart
./bin/service.sh log 50            # 另外还有 check / reset
```

`bin/run.sh` 是前台运行器：用 `flock` 保证只有一个实例，`python3 -u app.py` 的输出追加到
`data/service.log`。看护进程会重启退出的服务，但**整台机器重启之后**仍要手动 `start`
（或者交给 systemd 之类的守护）。

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

- 外部资源一个都不加载，内置模块也算；数据原样留在 `data/` 里，不做兜底改写
- 活动页（公示页、大屏、控制台）返回 503，给的是一张维护页：地址不变，每 15 秒自己重试，
  服务恢复后刷新就回到原来的内容
- 活动接口一律 503 加一句说明
- **平台自己的东西照常可改**：账号、角色、名单、短链、通知、配置
- **跟着模块走的事情做不了**：模板都依赖模块，所以新建活动会被拒并说明原因；模块开关同理，
  要改就改配置文件，或者先退出安全模式
- 只有站长能登录，其他账号会看到 `Service is under maintenance`
- 登录页与面板顶上各挂一条「服务处于安全模式」的横幅；面板的服务器管理里也有同一条提示

退出：把开关改回 `false` 再重启；或者由站长在面板「服务器管理」里点 [退出安全模式]，
服务会自己重启一次。

改开关的接口是 `POST /api/panel/service/safe_mode`（`{"on": true|false}`），站长加 sudo 才动得了，
进出都记审计。写的是 `config.json` 里的 `safe_mode`，别的键原样保留。重启服务的接口是
`POST /api/panel/service/restart`，同样站长加 sudo，也记审计。

### 崩溃自检

数据或配置坏掉的时候，服务会反复在同一处崩掉：看护进程隔一秒拉起一次，日志里堆满同一段回溯，
人却进不了面板。自检用来跳出这个循环（`core/startguard.py`）：

- 每次启动先记一笔「这一次要起了」（`data/startup.json`），初始化全部过完再记一笔「起来了」。
  中间崩掉就没有第二笔
- **连着三次没起来，再下一次自动按安全模式启动**：把 `safe_mode` 写进 `config.json`，
  这一次不搬移、不初始化活动数据（坏掉的那份原样留着），于是服务起得来，站长进得去
- 安全模式下活动页照样 503，与被谁切进来无关；日志里有一行
  `崩溃自检：连续 3 次启动失败，这一次按安全模式启动`，审计里有一笔
  `enter_safe_mode`，说明是 `reason: multiple crash detected`（面板上手动进出记的是
  `enter_safe_mode` / `exit_safe_mode`，说明 `reason: manual`）
- 修好之后按常规退出安全模式：面板「服务器管理 › 退出安全模式」，或把 `safe_mode` 改回
  `false` 再重启。起来一次就清零，不会因为几天前的崩溃又跳进安全模式
- 手工想清掉计数：删掉 `data/startup.json`（下一次启动从一开始数）

`data/startup.json` 里 `attempts` 是「这一次是连续第几次尝试」，`ok` 表示最近一次起来过，
`autoAt` 是最后一次被自检切进安全模式的时间。

## 日志

### 看护进程

`bin/service.sh start` 起的是 `bin/supervise.sh`，它在前台跑 `bin/run.sh`，app 退出就重新起来。
面板里的重启与安全模式开关都靠这一层：app 回完响应自己退出，看护进程把它拉起来，
安全模式在启动时重新读一次。所以：

- 停服务用 `./bin/service.sh stop`（它先停看护进程，再停 app），别直接 kill app
- `data/service.pid` 是 app 的 pid，`data/supervise.pid` 是看护进程的
- **面板「服务器管理」里的安全模式开关依赖这一层**：服务必须由 `bin/service.sh` 启动，
  页面上的进出才会自己重启。手工 `./bin/run.sh` 起的服务没有看护进程，页面上会写
  「服务管理未运行，无法自动重启」，只改配置不重启

## 日志

`data/service.log`。写到 8 MB 自己换一份：改名成 `.1`，旧的依次往后挪，最多留三份（`.1`~`.3`）。
服务是 `>>data/service.log` 起的，所以轮转是把同名文件重新开一份挂到 fd 1 上；日志写不出去不会
让请求失败。`LOTTERY_LOG_MAX` 改上限（字节），`0` 关掉轮转。

## 备份与恢复

备份由 `bin/backup.py` 做，包放在 `backups/`（不是 `data/` 里——备份不能跟被备份的数据一起被
`reset` 清掉）。默认取一份这样：

```sh
python3 bin/backup.py run          # 立刻做一份
python3 bin/backup.py list         # 有哪些、多大、含几个活动
python3 bin/backup.py verify 最新    # 对一遍 sha256 与条目
python3 bin/backup.py doctor       # 备份多久没做、活动有没有坏形态
```

包里有什么：

| 内容 | 说明 |
| --- | --- |
| `db/panel.sqlite3` | 账号、活动索引、名单与报名索引、令牌、助手存档、平台审计 |
| `db/lists.sqlite3` | 名单与人员库 |
| `events/<号>/data.sqlite3`、`audit.sqlite3`、`meta.json` | 在跑的活动的业务库与审计（一致快照） |
| `events/<号>/archive.tar.gz`、`meta.json` | 已归档的活动：带它的冻结件，不展开 |
| `files/avatars/`、`files/modules/`、`files/theme/` | 头像与实例自己导入的模块、屏幕包 |
| `identity/config.json`、`identity/assistant_prompt.md` | 身份信息：**进包但不自动恢复** |
| `manifest.json` | 格式版本、应用版本、时间、每个条目的 sha256、各库行数 |

库一律用 SQLite 的在线备份 API 取快照：事务一致、不含 WAL、体积就是主干那么大（本机是百人量级，
全部库的快照加起来不到 1 MB）。**日志与 `-wal`/`-shm`、`service.pid` 这类运行时文件不进包**；
`data/backup_settings.json`（备份自己的配置）也不进包——恢复数据不该把调度、留存与 WebDAV 凭据
一起倒回去。

恢复：

```sh
./bin/service.sh stop
python3 bin/backup.py restore 最新 --yes      # 恢复前会自动把当前状态收一份
./bin/service.sh start
```

- 默认只写回**数据**；`config.json` 与 `assistant_prompt.md` 要写回得加 `--with-identity`
- 恢复之后所有登录会话被清空，所有人要重新登录（否则三天前的 cookie 会跟着旧库一起复活）
- 恢复前的那份当前状态记成 `pre-restore`，恢复错了还能倒回去
- 服务还在跑时拒绝恢复：`-wal` 会被旧连接覆盖回去，这一步必须先停服务

设置（调度、留存、WebDAV）在 `data/backup_settings.json`，位置固定，不随 `data_dir` 变：

```sh
python3 bin/backup.py settings                  # 看一眼（密码打码）
python3 bin/backup.py settings set schedule.at=03:30
python3 bin/backup.py settings set webdav.url=https://dav.example/remote.php/dav/
```

### 自动备份

服务进程里有一个调度线程，每 5 分钟看一次"这一轮该不该做"，起来的那一刻也看一次——
**关机错过的窗口在启动时补上**（补一次，不会连着补三天）。容器里没有 systemd 也没有 cron，
所以定时只能放在进程内；如果宿主机有 systemd，也可以不用它、改用 timer 调
`python3 bin/backup.py auto`（同样的判断，到点才做）。

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 关掉之后只允许手动 `run` |
| `schedule.mode` | `daily` | `daily` 每天一次；`manual` 完全不自动做 |
| `schedule.at` | `03:30` | 触发时刻 `HH:MM`；写错了就当没开自动备份，不去猜 |
| `retention.daily` | `7` | 最近 7 天每天留最新一份 |
| `retention.weekly` | `4` | 最近 4 周每周留一份 |
| `retention.monthly` | `6` | 最近 6 个月每月留一份 |
| `retention.maxBytes` | `0` | 总字节上限，0 表示不限；超了从最老的开始删 |

三档取并集；`pre-restore`（恢复前自动留的那份）**永远不删**。手动看一眼会删哪些：

```sh
python3 bin/backup.py prune          # 只看
python3 bin/backup.py prune --yes    # 真删
```

调度这一段有自己的护栏（`core/taskguard.py`）：任务抛异常就记日志并计一次数，
**连续三次失败就停掉这个任务**、发一条站内通知、审计记 `task_stopped`——不会静悄悄地死掉。

### 推到 WebDAV

异地副本用 WebDAV 送出（坚果云、Nextcloud、群晖都行）。填好这一段再测一下：

```sh
python3 bin/backup.py settings set webdav.enabled=true
python3 bin/backup.py settings set webdav.url=https://dav.example/remote.php/dav/files/me/cae
python3 bin/backup.py settings set webdav.username=me
python3 bin/backup.py settings set webdav.password=应用密码
python3 bin/backup.py webdav test          # 连得上、目录建得出、写得进、删得掉
```

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `webdav.enabled` | `false` | 关着就只做本地备份 |
| `webdav.url` | — | 集合地址（走到放备份的那个目录为止），必须 `http(s)` 开头 |
| `webdav.path` | `/` | 在集合下面再套一层目录；不存在会自动 MKCOL 出来 |
| `webdav.username` / `password` | — | Basic 认证。密码建议用服务商给的**应用密码** |
| `webdav.verify` | `true` | 上传后 HEAD 一次，比对远端大小 |
| `webdav.prune` | `true` | 远端只留本地还留着的那些，多出来的删掉 |

怎么做的：

- 每次自动备份之后把新包 PUT 上去，然后让**远端与本地留存一致**——远端只留本地还留着的包名，
  多出来的删掉；**只删我们自己命名的文件**（`YYYYMMDD-HHMMSS-<种类>.tar.gz`），
  同一个目录里别人放的东西一概不碰
- **上传失败不影响本地备份**：本地的包已经落盘，远端只是异地副本。失败写进日志与审计
  （`backup_upload_failed`），用 `webdav test` 与 `webdav list` 排查
- TLS 证书照常校验，自签证书请先在这台机器上装好；不支持明文以外的降级
- 手动推一次：`python3 bin/backup.py webdav push 最新`；清远端：`webdav prune`；看远端：`webdav list`

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
| `owner_username` / `owner_name` | 站长的登录名与显示名 |
| `shortlink_per_event` | 静态设置：一个活动最多同时持有几条短链（默认 5），改完重启生效 |
| `safe_mode` | 静态设置：服务安全模式（默认 `false`），改完重启生效 |
| `delete_account_after_days` | 静态设置：账号要停用满几天才允许彻底删除（默认 3），改完重启生效 |
| `max_accounts` | 静态设置：这个实例最多能有多少个账号（默认 0，即不限），改完重启生效 |
| `data_dir` | 静态设置：数据放在哪（默认应用根目录下的 `data/`）。相对路径按应用根目录算；`LOTTERY_DATA_DIR` 环境变量优先 |
| `backups_dir` | 静态设置：备份包放在哪（默认应用根目录下的 `backups/`），备份与恢复都用它 |

「静态设置」指写在 `config.json` 里、改完必须重启才生效的项；界面上没有入口（面板里能改的那些
叫活动设置 / 平台设置，存在库里，立刻生效）。

`max_accounts` 数的是 `accounts` 表的行数：待审核、已停用、已封禁的账号都占名额，只有彻底删除
才腾出位置；站长账号也算一个。到上限之后注册接口回 403 并说明原因，已经在库里的账号照常登录、
审核与停用。把上限调低到现有账号数之下不会删人，只是不再接受新注册。

随机源：有 `/dev/hwrng` 就与内核的混合着用，没有就只用内核的 CSPRNG。想知道这台机器用的是哪一种，
看服务启动后在服务器上执行 `python3 -c "import sys; sys.path.insert(0,'core'); import rng; print(rng.source())"`。

环境变量可以覆盖：`LOTTERY_DATA_DIR`、`LOTTERY_PACKS_DIR`、`LOTTERY_PORT`、
`LOTTERY_VERSION`（测试用）、`LOTTERY_LOG_MAX`、`LOTTERY_ASSISTANT_PROMPT`。

## 相关文档

- [接口](../reference/api.md)
- [结构](../dev/architecture.md)
