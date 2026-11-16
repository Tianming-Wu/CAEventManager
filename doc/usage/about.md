# 关于 - 版本、仓库、作者与反馈

CAEventManager 是学生社团活动管理服务平台：一套服务同时承载主面板与多个活动。主面板管账号、
名单与报名，每个活动各有公示页、大屏、登记页与活动后台；活动的内容由模块决定，平台只提供外壳。
自托管、单进程运行，不引入第三方运行时依赖。

## 版本

- 版本号四段 `主.次.修.构`：主是架构或兼容性变化，次是一批新功能，修是行为变化与修复，
  构是每次上线加一，只增不减
- 页面上写在哪里：面板侧栏底部、使用文档页的标题旁、404 页底部
- 接口的每个回答都带着 `version`，页面据此决定要不要换新脚本
- 升级与回退的做法写在部署手册 `doc/ops/deploy.md` 里，那一份不在这个站点上

## 仓库

源码在 GitHub：[Tianming-Wu/CAEventManager](https://github.com/Tianming-Wu/CAEventManager)

- 许可证：MIT，见仓库里的 `LICENSE`
- 结构：入口为根目录 `app.py`，程序位于 `core/`，脚本位于 `bin/`，静态资源位于 `assets/`，
  页面外壳位于 `pages/`，内建资源（模块、静态资源）位于 `builtin/`
- 文档位于 `doc/` 下，索引位于 `doc/README.md`
- 计划与取舍记在仓库的 `ROADMAP.md` 里，包括明确不做的部分

## 作者与维护

- 设计与开发：Tianming Wu（GitHub [@Tianming-Wu](https://github.com/Tianming-Wu)）
- 著作权：Copyright (c) 2026 Tianming Wu
- 登录页底部的页脚写着作者；这个仓库面向社团自行部署：一套服务属于一个组织，不做多租户

## 反馈

- 缺陷与建议提到仓库的 issue：[提交 issue](https://github.com/Tianming-Wu/CAEventManager/issues)
- 提问前先看 [开始使用](start.md) 与 [术语表](../reference/glossary.md)；
  装机、备份这类事问维护这台机器的人
- 线上部署实例的问题由该实例的站长处理：账号、权限与数据都在部署实例中，不进入仓库

## 相关文档

- [开始使用](start.md)
- [术语表](../reference/glossary.md)
- [服务器与审计](server.md)
