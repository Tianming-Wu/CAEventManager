# CAEventManager - 学生社团活动管理平台

CAEventManager 是可自行部署的学生社团活动管理平台，提供活动管理、名单、报名、现场签到及抽奖功能。

## 环境要求

| 场景 | 要求 |
| --- | --- |
| 本地部署 | Python 3。使用服务脚本时需要 Linux 和 `flock` |
| 远程部署 | 满足服务运行要求。使用远程部署脚本时，需要 SSH 客户端及目标 Linux 系统上的 SSH 访问和 `tar` |
| 界面测试 | Microsoft Edge 和 Python 的 Playwright 包 |

## 安装与启动

1. 将应用文件放入部署目录。远程部署可使用部署脚本的 `-NoRestart` 选项，待配置完成后再启动。
2. 在应用根目录创建 `config.json`。将 `public_base_prefix` 设置为用户访问实例的完整基础地址，包含协议及实际使用的端口和挂载路径，例如 `https://events.example.org/activities`。按需设置监听地址 `host` 和监听端口 `port`。完整示例、静态设置和环境变量见[部署配置](doc/ops/deploy.md#配置)。
3. 在应用根目录启动服务。

   ```sh
   ./bin/service.sh start
   ```

4. 通过配置的对外地址访问实例。使用站长登录名（默认为 `owner`）及数据目录中 `owner_password.txt` 保存的初始密码登录，随后修改密码。

服务脚本支持通过面板重启服务，以及在切换安全模式后自动重启。前台调试可使用 `python3 app.py --host 127.0.0.1`。直接启动时，在面板切换安全模式后须手动重启。手动备份和定时备份均由应用提供。参见[首次部署](doc/ops/deploy.md#首次部署)。

## 许可证

采用 MIT 许可证。参见 [LICENSE](LICENSE)。

## 相关文档

- [文档目录](doc/README.md)
- [开始使用](doc/usage/start.md)
- [部署与运维](doc/ops/deploy.md)
- [路线图](ROADMAP.md)
