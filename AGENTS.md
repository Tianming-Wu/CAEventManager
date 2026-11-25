# 仓库协作约定 - CAEventManager 的修改、验证与提交

产品说明和本机启动方法见 [README](README.md)。文档分类及写作规范见[文档目录](doc/README.md)。

## 项目结构

入口为根目录的 `app.py`。程序模块位于 `core/`，运行脚本位于 `bin/`，开发工具位于 `tools/`。静态资源位于 `assets/`，页面模板位于 `pages/`，外观包位于 `theme/`，内置模块及预设模板位于 `builtin/`。

## 文档同步

新增、修改或移除功能时，同步更新运维助手可读取的对应文档，范围包括 `doc/usage/`、`doc/reference/` 和模块的 `doc.md`。核对页面入口、界面内容和按钮名称。代码及对应文档应纳入同一提交。

## 验证

开发过程中运行 `changed`，由脚本按工作区改动选择测试。修改共享组件（包括 `assets/js/panel.js`、`assets/css/panel.css`、`pages/`、`app.py` 和 `tools/`）或影响范围不明确时，运行完整套件。提交前运行完整套件，并将源文件行尾统一为 LF。

```powershell
python tools/run_suites.py changed
python tools/run_suites.py unit panel_test panel_ui_test smoke ui safemode modules
python tools/normalize_eol.py
```

范围明确时，可用 `--only` 选择面板页面测试分组。

```powershell
python tools/run_suites.py panel_ui_test --only=assistant
python tools/run_suites.py panel_ui_test --only=shell
python tools/run_suites.py panel_ui_test --only=data,ops
python tools/run_suites.py panel_ui_test --only=docs
```

分组为 `assistant`、`docs`、`shell`、`data`、`mods`、`admin`、`ops`。省略 `--only` 或指定 `all` 时运行全部分组。

## 版本号

版本号采用「主.次.修.构」四段格式，使用 `python tools/bump_version.py [major|minor|patch]` 更新。

- **主版本**用于架构或兼容性变更，更新前须获得用户确认。
- **次版本**用于一组新功能发布。
- **修订版本**用于行为调整或缺陷修复。
- **构建号**在提交并上线时递增，始终保留累计值。

## 提交与运行数据

每个提交聚焦一项变更，仅暂存相关路径。提交信息概述变更内容，省略理由、过程、测试结果及作者信息。参见[提交信息](doc/dev/architecture.md#提交信息)。

仓库修改应避开实例的 `data/`、`config.json` 和自定义外观包。修改已部署实例的数据前须先备份。

## 相关文档

- [项目说明](README.md)
- [文档目录及写作规范](doc/README.md)
- [项目架构](doc/dev/architecture.md)
