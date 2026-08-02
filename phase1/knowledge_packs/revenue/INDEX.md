# Revenue Knowledge Pack — Draft

> 历史状态说明：本目录保留Phase 1盘点模板，不代表当前Weekly Workflow准备度。
> 已确认的收入资产以`phase1_5/assets/`和统一Status Index为准。

## 目的

保存 Weekly Business Report 所需的已确认收入业务知识。此 Pack 不保存通用数据处理能力，也不保存 Workflow 步骤。

## 当前文件

| 文件 | 用途 | 当前状态 |
|---|---|---|
| `business_definition.template.md` | 收入范围、术语、包含/排除项 | 待填写 |
| `dimensions.template.yaml` | Revenue 标准业务维度 | 待填写 |
| `business_rules.template.yaml` | 收入业务规则 | 待填写 |
| `validation_rules.template.yaml` | 收入数据和指标校验 | 待填写 |
| `metric_manifest.template.yaml` | 本 Pack 引用的 Metric_ID | 待填写 |
| `../../data_sources/revenue_outlook.template.yaml` | 收入 Outlook 来源 | 待填写 |
| Phase 1 盘点工作簿“Revenue字段映射” | 字段 Mapping | 待填写 |

## 建议盘点顺序

1. 提供收入邮件、附件和成品 Excel 样例。
2. 定义收入数据的最细粒度。
3. 确认客户、产品/资源等标准维度。
4. 建立字段 Mapping。
5. 记录当前人工使用的收入规则和核对方法。
6. 将正式指标登记到 Metric Library，仅在 Manifest 中引用 Metric_ID。

## 发布门槛

- 所有必需字段已有映射或明确阻断。
- 每条 Active 规则有 Owner、生效日期和来源。
- 核心收入指标有验证方式。
- 未解决口径冲突保留为异常，不允许静默选择。
