# Phase 1：Business Asset Discovery & MVP Workflow Design

## 1. 本阶段输出结论

本阶段只建立“可填写的业务资产框架”和“可评审的 Weekly Workflow”，不连接 Outlook、公司数据平台，也不实现指标计算或 Draft 创建。

待确认事项按实现影响分为 P0、P1、P2。只要求先收集 P0 的最小必要信息；不要求一次性补齐所有业务规则和异常案例。

## 2. 需要提供的业务资产清单

### 2.1 P0：影响 MVP 实现

| ID | 需要提供的资产 | 最小可接受内容 | 用途 | 对应模板 |
|---|---|---|---|---|
| A01 | 周期定义 | “本周”、截止日、跨月/跨季度归属的当前人工规则 | 确定 Run 周期及历史比较 | 盘点工作簿“周期配置”；Workflow Config |
| A02 | 收入邮件样例 | 最近 2–4 次邮件主题、发件人、到达时间及附件；可脱敏 | 建立邮件识别与附件选择规则 | 数据源配置、Revenue Source Config |
| A03 | 收入附件样例 | 至少 2 个正常样例，最好含一次字段变化或异常 | 字段 Mapping、类型和质量规则 | Revenue Field Mapping |
| A04 | 收入输出 Excel | 当前正式模板及 2–4 次历史成品 | 确定输出字段、版式、文件名和核对逻辑 | 模板登记表 |
| A05 | 库存平台说明 | 访问入口、权限方式、当前人工导出步骤 | 确定 MVP 获取方式 | Inventory Source Config |
| A06 | 库存导出样例 | 至少 2 个不同周的原始导出文件 | 字段 Mapping、粒度和更新检查 | Inventory Field Mapping |
| A07 | Revenue 业务维度 | 客户、产品/资源等维度的定义和样例值 | 形成标准数据模型 | Revenue Dimensions |
| A08 | Inventory 业务维度 | 产品/资源、位置/渠道等实际维度 | 形成标准数据模型 | Inventory Dimensions |
| A09 | 当前周报指标 | 指标名、业务定义、公式、Owner、验证方式、历史展示方式 | 建立 Metric Library v1 | 盘点工作簿“指标登记” |
| A10 | 周报邮件样例 | 最近 2–4 次已发送邮件或 Draft；可脱敏 | 确定 Draft 模板与必填项 | 模板登记表 |
| A11 | 人工核对方法 | 当前每一步如何判断收入、库存、最终报告正确 | 建立 Validation Rules | 两个 Pack 的 Validation Rules |

### 2.2 P1：试运行前补充

| ID | 需要提供的资产 | 最小可接受内容 | 对应模板 |
|---|---|---|---|
| A12 | 确认人清单 | 收入确认人、最终 Draft 检查人、替补人 | Workflow Checkpoint |
| A13 | 数据迟到经验 | 常见迟到时长、当前如何处理 | Source Config / Workflow Config |
| A14 | 已知异常样例 | 3–5 个高频、影响大的案例 | Knowledge Pack / Solved Cases |
| A15 | 历史指标样例 | 支持至少 4 周环比验证 | Metrics Store 设计输入 |
| A16 | 权限与保存限制 | 原始附件能否落地、临时文件保存位置和时长 | Data Source / Retention |

### 2.3 P2：后续逐步补充

| ID | 资产 | 当前处理 |
|---|---|---|
| A17 | 最终历史指标保存年限 | 模板保留配置项，初始值 `TBD` |
| A18 | 完整超时与升级机制 | MVP 先记录超时，不设计复杂升级链 |
| A19 | Draft 动态收件人规则 | MVP 可使用固定占位或人工选择 |
| A20 | 全量异常分类 | 先覆盖字段、数据源、指标、模板五类关键异常 |

## 3. 资产提交要求

- 可以脱敏，但必须保留字段结构、数据类型、日期格式和业务关系。
- 样例文件注明业务周期、生成时间和是否为最终版本。
- 每个业务定义或规则至少有一名 Owner。
- 如果不同人员口径不一致，应同时记录冲突版本，不提前合并。
- 历史报告只作为盘点证据；确认后的知识进入结构化资产，历史文件不作为运行时知识库。

## 4. 每项资产对应的配置模板

| 资产类型 | 主模板 | 适用对象 |
|---|---|---|
| 周期定义 | `workflow_config.template.yaml` 的 `period_definition` | Weekly Workflow |
| 数据源 | `data_source_config.template.yaml` | 所有来源 |
| 收入 Outlook 来源 | `revenue_outlook.template.yaml` | Revenue |
| 库存平台来源 | `inventory_platform.template.yaml` | Inventory |
| 业务定义 | `business_definition.template.md` | Revenue / Inventory |
| 业务维度 | `dimensions.template.yaml` | Revenue / Inventory |
| 字段映射 | 盘点工作簿“Revenue字段映射”“Inventory字段映射” | Revenue / Inventory |
| 业务规则 | `business_rules.template.yaml` | Revenue / Inventory |
| 验证规则 | `validation_rules.template.yaml` | Revenue / Inventory |
| 指标定义 | 盘点工作簿“指标登记”及 `metric_definition.template.yaml` | Metric Library |
| 模板登记 | 盘点工作簿“模板登记” | Excel / Outlook |
| 人工确认 | Workflow Config 的 `checkpoints` | Weekly Workflow |

## 5. 盘点顺序

```mermaid
flowchart LR
    P["周期定义"] --> S["数据源与样例"]
    S --> D["业务维度与字段 Mapping"]
    D --> R["业务规则与验证规则"]
    R --> M["Metric Library v1"]
    M --> T["输出模板与确认点"]
    T --> F["MVP 设计冻结评审"]
```

不应先从“自动化怎么做”开始。先确认人工流程中真正使用的数据、口径和校验，再决定实现方式。

## 6. Phase 1 完成标准

- P0 资产均已收到，或有明确 Owner 和预计补充时间。
- Revenue / Inventory 标准维度与字段 Mapping 已形成 Draft。
- MVP 核心指标已登记，且每个指标有定义、公式、Owner 和验证方式。
- 两个数据源配置已从模板转为可评审 Draft。
- Weekly Workflow 的输入、步骤、依赖、人工确认、异常阻断和输出均无歧义。
- 收入 Excel 与 Outlook Draft 模板已登记。
- 未确认项仍保留为 `TBD`，没有被实现假设替代。

## 7. 进入下一阶段的门槛

只有 P0 项达到 `已批准` 或存在经 Owner 接受的临时规则，才能进入技术实现。P1/P2 未完成可以保留，但必须说明风险和计划。
