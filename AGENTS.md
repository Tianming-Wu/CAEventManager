# AGENTS.md

在 CAEventManager 仓库里工作的约定。产品说明见 [README.md](README.md)，文档目录见
[doc/README.md](doc/README.md)。

## 文档

- 根目录只有入口 `app.py`、文档与许可证；程序模块在 `core/`，脚本在 `bin/`，开发工具在 `tools/`，
  静态资源在 `assets/`，页面外壳在 `pages/`，外观包在 `theme/`，内建模块与预设的模板在 `builtin/`
- 用户文档在 `doc/usage/`、`doc/reference/`，以及各模块的 `doc.md`。运维助手只读这三处
- 开发与运维文档在 `doc/dev/`、`doc/ops/`
- 一份文件只讲一件事；标题写成 `# 名称 - 一句话`；末节是「相关文档」
- 改代码与改它对应的文档放在同一次提交里
- **新增、修改或移除功能时，必须同步改助手读得到的文档**：`doc/usage/`、`doc/reference/`、
  对应模块的 `doc.md`。写清入口在哪一页、页面上有什么、按钮叫什么。运维助手看不到界面，
  只能照文档回答；漏改的后果是它给出一个错误的位置

## 改动之后

```powershell
python tools/run_suites.py changed                                   # 按改动挑最小的那几套
python tools/run_suites.py unit panel_test panel_ui_test smoke ui safemode modules   # 全套件
python tools/normalize_eol.py                                        # 行尾统一为 LF
python tools/bump_version.py                                         # 推进版本号
```

**开发中间用 `changed`，提交之前跑一次全套。** 它会看 `git status` 里改过的文件：

- 只动了某个区域（`core/assistant.py` / 名单数据层 / 模块 / 文档……）→ 只跑那几套，
  页面测试也只跑对应那一组
- 动了共用件（`assets/js/panel.js`、`assets/css/panel.css`、`pages/`、`app.py`、`tools/`），
  或者说不清影响范围 → 退回全套。**这些文件是真共用的，别拿"应该没事"去赌**

页面测试可以自己指定分组，省掉整套 `panel_ui_test`（一次全套约三分钟，单组二十几秒）：

```powershell
python tools/run_suites.py panel_ui_test --only=assistant     # 运维助手：对话、批准条、抽屉、Markdown
python tools/run_suites.py panel_ui_test --only=shell         # 侧栏、路由、导航键、面包屑、布局档
python tools/run_suites.py panel_ui_test --only=data,ops      # 名单报名 + 服务器/审计/主题/弹窗
python tools/run_suites.py panel_ui_test --only=docs          # 使用文档那一页（独立骨架）
```

分组是 `assistant` / `docs` / `shell` / `data` / `mods` / `admin` / `ops`；不写或写 `all` 就是全套。
每组打印自己的用时，`run_suites.py` 最后再报一遍合计，慢在哪儿一眼能看出来。

## 版本号

四段 `主.次.修.构`（`python tools/bump_version.py [major|minor|patch]`）：

- **主**：要用户确认才能动——架构或兼容性变了才动
- **次**：一批新功能落地，自行决定
- **修**：行为变化或一批修复，开发过程中该动就动，自行决定
- **构**：每次提交并上线都 +1，**只增不减、永不清零**（工具已经保证，不要手工清零）

## 提交

- 消息只写改了什么。不写理由、过程、测试结果与作者信息，规则见
  [doc/dev/architecture.md](doc/dev/architecture.md) 的「提交信息」
- 一个提交一件事；只 add 该提交涉及的路径

## 不要动

- `data/`、`config.json`、`theme/<自己装的>`：运行时数据，已在 `.gitignore` 中排除
- 已部署实例里的活动、名单与账号都在 `data/` 里，改动前先备份

## 本机运行

```sh
python3 app.py
```

首次启动会生成 `config.json` 与 `data/owner_password.txt`，不需要部署即可在仓库里直接运行。
