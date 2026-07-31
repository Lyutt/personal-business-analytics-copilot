# Phase 1 入口

> Business Asset Discovery & MVP Workflow Design

## 本阶段目标

在不编写运行代码的前提下，把 Weekly Business Report MVP 所需的业务知识、数据源、指标和流程转成可填写、可评审、可版本化的资产。

## 交付物

| 交付物 | 文件 |
|---|---|
| Phase 1 总方案与业务资产清单 | `Business_Asset_Discovery_and_MVP_Workflow_Design.md` |
| Weekly Workflow 详细设计 | `workflows/weekly_business_report/WORKFLOW.md` |
| Workflow 配置模板 | `workflows/weekly_business_report/workflow_config.template.yaml` |
| Revenue Knowledge Pack | `knowledge_packs/revenue/` |
| Inventory Knowledge Pack | `knowledge_packs/inventory/` |
| Metric Library 说明与单指标模板 | `metric_library/` |
| 通用及来源专属 Data Source Config | `data_sources/` |
| 非技术人员填写用盘点工作簿 | `outputs/phase1/Business_Asset_Discovery_Template.xlsx` |

## 优先级

### P0：阻塞 MVP 设计冻结

- 周期定义与截止日期规则。
- 收入 Outlook 邮件识别方式和附件格式。
- 库存平台访问/导出方式和文件格式。
- Revenue / Inventory 标准业务维度。
- 当前周报核心指标的定义、公式、Owner 和验证方式。
- 收入 Excel 与周报 Draft 的真实模板或样例。

### P1：阻塞试运行，但不阻塞配置结构建立

- 人工确认人和替补人。
- 数据迟到及补跑规则。
- 核心数据质量阈值。
- 历史比较需要的最小周期。

### P2：暂不阻塞 MVP

- 历史指标最终保存年限。
- 完整超时升级机制。
- Draft 收件人动态配置。
- 所有长尾异常处理细节。

## 使用规则

1. 先填写盘点工作簿，未知项标为 `待确认`，不要猜测。
2. 每个事实标注来源、Owner 和确认状态。
3. 只有 `已批准` 的内容可以转为 Active 配置。
4. 本阶段模板中的占位符不代表正式业务口径。
5. 完成 P0 资产盘点后，才进入受控实现设计评审。
