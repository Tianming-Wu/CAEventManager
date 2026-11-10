# AGENTS.md

在 CAEventManager 仓库里工作的约定。产品说明见 [README.md](README.md)，文档目录见
[doc/README.md](doc/README.md)。

## 文档

- 使用者文档在 `doc/usage/`、`doc/reference/`，以及各模块的 `doc.md`。运维助手只读这三处
- 开发与运维文档在 `doc/dev/`、`doc/ops/`
- 一份文件只讲一件事；标题写成 `# 名称 - 一句话`；末节是「相关文档」
- 改代码与改它对应的文档放在同一次提交里
- **新增、修改或移除功能时，必须同步改助手读得到的文档**：`doc/usage/`、`doc/reference/`、
  对应模块的 `doc.md`。写清入口在哪一页、页面上有什么、按钮叫什么。运维助手看不到界面，
  只能照文档回答；漏改的后果是它给出一个错误的位置

## 改动之后

```powershell
python tools/run_suites.py unit panel_test panel_ui_test smoke ui   # 全套件
python tools/normalize_eol.py                                        # 行尾统一为 LF
python tools/bump_version.py                                         # 推进版本号
```

## 提交

- 消息只写改了什么。不写理由、过程、测试结果与作者信息，规则见
  [doc/dev/architecture.md](doc/dev/architecture.md) 的「提交信息」
- 一个提交一件事；只 add 该提交涉及的路径

## 不要动

- `data/`、`config.json`、`screen_packs/`：运行时数据，已在 `.gitignore` 中排除
- 已部署实例里的活动、名单与账号都在 `data/` 里，改动前先备份

## 本机运行

```sh
python3 app.py
```

首次启动会生成 `config.json` 与 `data/owner_password.txt`，不需要部署即可在仓库里直接运行。
