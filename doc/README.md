# 文档目录 - 按读者和任务查阅文档

使用文档查看器和运维助手可访问使用说明、参考资料及模块说明。站长还可在文档查看器中查阅部署与运维文档。

| 分类 | 读者 | 内容 |
| --- | --- | --- |
| [使用说明](usage/) | 平台使用者 | 页面入口与操作步骤 |
| [参考](reference/) | 平台使用者和开发者 | 术语、界面描述和接口 |
| [部署与运维](ops/) | 部署维护人员 | 部署、配置、日志、备份与恢复 |
| [开发](dev/) | 开发者 | 项目架构、模块及界面规范 |
| 模块说明 | 活动使用者 | [奖票抽奖](../builtin/modules/nwuca.lottery/doc.md)、[讲座](../builtin/modules/nwuca.talk/doc.md) |

## 使用说明

- **开始使用**：[注册与面板导航](usage/start.md)、[账号与角色](usage/account.md)、[界面设置](usage/theme.md)、[关于](usage/about.md)
- **活动**：[创建与归档](usage/events.md)、[活动页面与现场](usage/event-site.md)、[预设](usage/presets.md)、[短链](usage/shortlinks.md)
- **名单与报名**：[名单](usage/rosters.md)、[报名](usage/signups.md)、[审批](usage/approval.md)、[导出与打印](usage/output.md)
- **平台管理**：[平台管理页](usage/platform-pages.md)、[组织](usage/orgs.md)、[导入账号](usage/account-import.md)、[服务器与审计](usage/server.md)、[sudo 模式](usage/sudo.md)、[通知与服务提示](usage/notices.md)
- **运维助手**：[操作说明](usage/assistant.md)、[附加提示词](usage/assistant-prompt.md)

## 参考

- [术语表](reference/glossary.md)与[界面描述写法](reference/ui-naming.md)
- [接口](reference/api.md)与 [API 令牌](reference/api-tokens.md)
- [表格导入导出接口](reference/api-tables.md)、[服务有效性接口](reference/api-service.md)与[短链接口](reference/api-shortlink.md)

## 部署与开发

- [部署与运维](ops/deploy.md)
- [备份](ops/backup.md)与[服务器特性](ops/features.md)
- [项目架构](dev/architecture.md)、[运维助手设计](dev/assistant.md)、[界面规范](dev/ui-spec.md)
- [模块开发指南](dev/modules.md)、[模块接口](dev/module-interface.md)

## 文档编写约定

1. 按读者的任务选择内容。使用说明提供页面入口、操作步骤、适用权限与结果。开发文档说明现行接口、约束及维护所需的实现细节。
2. 每篇聚焦一项主题。标题采用 `# 名称 - 概述` 格式，末节为「相关文档」。共用内容集中在对应主题中，由其他文档链接引用。
3. 使用简洁的书面语。以[术语表](reference/glossary.md)为准，并按[界面描述写法](reference/ui-naming.md)标示页面、位置及按钮。仅图标按钮应补充可识别的图标描述。
4. 核对功能入口、操作流程、配置名、接口名、默认值及上限。代码和对应文档一并更新。
5. 使用相对链接指向实际存在的文件，可用 `#章节标题` 指向章节。图片使用相对路径，格式为 PNG、JPEG、WebP、GIF 或 SVG。
6. 省略开发、测试及部署环境的专有信息，操作系统类型可按需注明。省略实现沿革、已弃用方案及与当前操作无关的理由。
7. 软件著作权登记材料分别取自使用说明和开发文档。

## 相关文档

- [项目说明](../README.md)
- [仓库协作约定](../AGENTS.md)
