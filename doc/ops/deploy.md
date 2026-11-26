# 部署与运维 - 安装、配置与服务维护

应用运行需要 Python 3。远程部署脚本通过 SSH 向 Linux 系统上传文件，目标系统须提供 `tar`。使用服务脚本还需要 `flock`。

## 可选依赖

组织外观需要服务端按请求宽度缩图，这一步依赖 [Pillow](https://python-pillow.org/)（`requirements.txt`）。部署脚本会在部署目录里建一个 `.venv` 并装好它，`bin/run.sh` 检测到 `.venv/bin/python3` 就用它，没有就用系统的 `python3`。

没装上（例如目标机访问不了软件源）不影响服务启动，只是服务器端不再缩图：外观接口一律返回原图，浏览器上传时预生成的那几档小图仍然可用。手动安装：

```sh
cd ~/apps/eventmanager
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./bin/service.sh restart
```

`./.venv/bin/python3 -c "import PIL; print(PIL.__version__)"` 有输出即视为可用。缩出来的图缓存在 `data/org_covers/.cache/`，累计超过 96 MB 时先丢最旧的；改动外观图会让缓存的键随之失效。

## 首次部署

1. 将应用文件放入部署目录。使用远程部署脚本时，指定 `-NoRestart`，完成配置后再启动。
2. 在应用根目录创建 `config.json`，设置对外地址和监听参数。参见[配置](#配置)。
3. 使用反向代理时，将对外地址的请求转发至应用监听地址。通过子路径发布时，核对页面、静态资源与接口请求均可通过该路径访问。
4. 在应用根目录执行 `./bin/service.sh start`。使用站长登录名及数据目录下 `owner_password.txt` 保存的初始密码登录，随后修改密码。
5. 从使用者所在的网络核对平台地址、活动分享链接和二维码。

## 更新部署

```powershell
.\bin\deploy.ps1 -Remote user@host
.\bin\deploy.ps1 -Remote user@host -NoRestart
```

默认部署目录为 `apps/eventmanager`，可用 `-Dest` 指定。脚本上传并验证文件后重启服务，指定 `-NoRestart` 时仅同步和验证文件。

同步会移除目标目录中不再随应用发布的旧文件。`data/`、`backups/`、`config.json` 及实例自定义的模块、预设和外观保留。同步前应确认备份可用。

### 版本号

发布前使用 `python tools/bump_version.py` 更新版本号。四段版本号分别表示主版本、次版本、修订版本及构建号。更新其他版本段时，构建号仍继续递增。参见[版本号约定](../../AGENTS.md#版本号)。

更新部署后，客户端根据服务版本更新页面。面板存在待提交内容时会提示刷新，抽奖大屏等待当前动画结束后更新。

## 服务脚本

在应用根目录执行以下命令。

```sh
./bin/service.sh start
./bin/service.sh status
./bin/service.sh stop
./bin/service.sh restart
./bin/service.sh log 50
```

服务脚本启动看护进程。应用异常退出时，看护进程会尝试重新启动。停止服务时使用 `./bin/service.sh stop`。操作系统重启后须重新启动服务，或由系统服务管理器负责启动。

前台调试可使用 `python3 app.py`。通过面板重启以及切换安全模式后自动重启依赖看护进程。直接启动时，切换安全模式后须手动重启。手动备份和定时备份均由应用提供。

## 安全模式

将 `config.json` 中的 `safe_mode` 设为 `true` 并重启。站长也可在「服务器管理」中点击 [进入安全模式]，通过密码确认后启用。

安全模式期间，站长仍可登录并维护平台账号、名单和配置。模块与外部资源停止加载，活动页面显示维护提示，活动接口返回 HTTP 503，活动数据保留。

排除故障后，将 `safe_mode` 恢复为 `false` 并重启，或在「服务器管理」点击 [退出安全模式]。手动切换及自动进入均会记录在审计日志中。

### 崩溃自检

连续三次启动未完成时，下一次启动会自动启用安全模式并暂停活动数据的迁移和初始化。服务日志记录连续失败次数，审计日志记录自动进入安全模式的原因。成功启动后，连续失败计数重置。

## 日志

服务脚本将运行日志保存在 `data/service.log`。使用 `./bin/service.sh log 50` 查看最近 50 行。默认轮转阈值为 8 MB，最多保留三份旧日志。可用 `LOTTERY_LOG_MAX` 设置字节上限，值为 `0` 时停用轮转。

平台审计日志可在「审计日志」查看。活动操作记录位于相应活动后台。参见[服务器与审计](../usage/server.md)。

## 备份与恢复

备份默认保存在应用根目录的 `backups/`，可通过 `backups_dir` 调整。可在面板「服务器管理」中执行备份，也可使用下列命令。

```sh
python3 bin/backup.py run
python3 bin/backup.py list
python3 bin/backup.py verify 最新
python3 bin/backup.py doctor
```

备份包含数据库的一致性快照及以下条目。运行日志、临时文件及备份设置不纳入备份。

| 条目 | 内容 |
| --- | --- |
| `db/panel.sqlite3`、`db/lists.sqlite3` | 平台数据、名单和人员数据 |
| `events/` | 活动数据、审计日志或归档包，以及活动元数据 |
| `files/` | 头像、自定义模块及展示屏幕 |
| `identity/` | 实例配置及助手附加提示词 |
| `manifest.json` | 格式和应用版本、创建时间及文件校验值 |

恢复前先校验目标备份并停止服务。

```sh
./bin/service.sh stop
python3 bin/backup.py restore 最新 --yes
```

可用实际备份文件名替换「最新」。确认备份选择后添加 `--yes` 执行恢复。恢复成功后，使用 `./bin/service.sh start` 启动服务。恢复前会自动保存当前状态，类型标记为 `pre-restore`。默认恢复应用数据，需同时恢复实例配置和助手附加提示词时，指定 `--with-identity`。恢复完成后，各账号须重新登录。备份和恢复结果均记录在审计日志中。

### 自动备份

可在「服务器管理」的「备份」卡片或通过 `python3 bin/backup.py settings` 查看设置。命令行可使用 `python3 bin/backup.py settings set schedule.at=03:30` 设置触发时刻。服务运行时定期检查计划，启动时也会检查并补做最近一次到期的备份。

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 启用自动备份 |
| `schedule.mode` | `daily` | `daily` 每日执行，`manual` 仅手动执行 |
| `schedule.at` | `03:30` | 每日触发时刻，格式为 `HH:MM` |
| `retention.daily` | `7` | 最近 7 天，每天保留最新一份 |
| `retention.weekly` | `4` | 最近 4 周，每周保留最新一份 |
| `retention.monthly` | `6` | 最近 6 个月，每月保留最新一份 |
| `retention.maxBytes` | `0` | 总字节上限，`0` 表示不限 |

日、周、月留存规则取并集。最近 24 小时内的备份及恢复前备份不参与清理。使用 `python3 bin/backup.py prune` 预览，添加 `--yes` 执行清理。若调度任务连续三次异常，平台会停止该任务，发送站内通知并记录审计。

### WebDAV 副本

在「备份」卡片的「WebDAV（异地副本）」中填写连接信息，通过 [测试 WebDAV] 验证访问及读写权限。

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `webdav.enabled` | `false` | 启用异地副本 |
| `webdav.url` | — | 以 HTTP 或 HTTPS 开头的集合地址 |
| `webdav.path` | `/` | 集合下的子目录，缺失时自动创建 |
| `webdav.username` / `webdav.password` | — | WebDAV 登录凭据 |
| `webdav.verify` | `true` | 上传后校验远端文件大小 |
| `webdav.prune` | `true` | 清理本地已移除的受管备份副本 |

自动备份完成后上传副本。上传失败时保留本地备份，并记录日志与审计。可用 `python3 bin/backup.py webdav test` 检查连接，使用 `webdav list` 查看远端备份，或使用 `webdav push 最新` 手动上传。HTTPS 连接须通过证书验证。

## 配置

在应用根目录创建 `config.json`，也可用 `LOTTERY_CONFIG` 指定文件路径。省略的设置使用默认值，修改后须重启服务。

以下示例适用于同机反向代理。请将对外地址替换为实际访问地址。

```json
{
  "host": "127.0.0.1",
  "port": 8931,
  "public_base_prefix": "https://events.example.org/activities",
  "owner_username": "owner",
  "owner_name": "站长"
}
```

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `0.0.0.0` | 应用监听地址 |
| `port` | `8931` | 应用监听端口 |
| `public_base_prefix` | `http://127.0.0.1:8931` | 实例的完整对外基础地址，包括协议、域名或 IP、非默认端口及挂载路径 |
| `owner_username` / `owner_name` | `owner` / `站长` | 站长登录名及显示名 |
| `shortlink_per_event` | `5` | 单个活动可同时持有的短链数量 |
| `safe_mode` | `false` | 启动时是否进入安全模式 |
| `delete_account_after_days` | `3` | 允许彻底删除账号前的最短停用天数 |
| `max_accounts` | `0` | 账号总数上限，`0` 表示不限。待审核及停用等状态的账号计入总数 |
| `data_dir` | `data/` | 数据目录，相对路径以应用根目录为基准 |
| `backups_dir` | `backups/` | 备份目录，相对路径以应用根目录为基准 |
| `trust_forwarded_ip` | `false` | 是否使用反向代理提供的客户端地址 |

降低账号上限不会删除已有账号。达到上限时，平台停止接收新注册申请。

仅在应用端口无法被外部客户端直接访问，且反向代理正确写入客户端地址时，启用 `trust_forwarded_ip`。启用后，平台使用 `X-Forwarded-For` 最右侧的有效地址或 `X-Real-IP`，无有效值时使用连接对端地址。

### 环境变量

- `LOTTERY_HOST`、`LOTTERY_PORT` 可覆盖配置中的监听地址和端口。启动参数优先。
- `LOTTERY_DATA_DIR` 可覆盖数据目录。`LOTTERY_PACKS_DIR` 用于指定外观包目录。
- `LOTTERY_LOG_MAX` 设置日志轮转阈值。`LOTTERY_ASSISTANT_PROMPT` 指定助手附加提示词文件。
- `LOTTERY_VERSION` 可覆盖服务报告的版本，用于验证。

### 换域名或换挂载前缀

1. 调整反向代理和域名解析，确认新地址可转发至应用。
2. 将 `public_base_prefix` 更新为完整的新地址并重启服务。
3. 从新地址核对平台、活动及新生成的链接和二维码。需要继续使用已分发的旧链接时，应在迁移期间保留旧地址的访问能力。

## 自检

部署后可使用以下命令核对服务和活动端点。请替换示例地址和活动访问凭据。

```sh
python3 tools/smoke_test.py http://127.0.0.1:8931
python3 tools/prod_check.py https://events.example.org/activities EVENT_TOKEN
```

## 相关文档

- [项目说明](../../README.md)
- [服务器与审计](../usage/server.md)
- [接口](../reference/api.md)
