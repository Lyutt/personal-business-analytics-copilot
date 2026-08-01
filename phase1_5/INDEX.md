# Phase 1.5 — Workflow Architecture Optimization

## 当前状态

- Workflow Architecture Optimization v2：已确认。
- Business Asset Initialization：进行中。
- Data Source Inventory：已完成，MVP范围共确认3个独立数据获取入口。
- Dataset / Query Asset Inventory：本轮录入完成。
- Dataset标准化与Readiness Gate：Weekly范围完成；Rolling Deck
  “业务线”Sheet与季度结算备选Sheet的增量Field Mapping已通过Delta Gate。
- Weekly Business Report Pipeline Registry：已完成，12条Pipeline全部通过
  Registry Readiness Gate。
- Field Mapping Initialization：原始9个Dataset范围及Revenue业务线增量范围已完成并通过Gate。
- Business Rule Initialization：进行中；Revenue范围已完成Readiness Gate。
- Revenue Business Rule状态：6条Approved。
- 当前收口点：Revenue Business Rule Initialization已完成，未自动进入下一阶段。
- 当前基线：[Phase 1.5 Baseline Closure — 2026-07-31](BASELINE_CLOSURE_2026-07-31.md)。
- 整体 Personal Business Analytics Copilot 架构：不变。
- 代码实现：未开始。

## 已确认架构

[Weekly Business Report Workflow Architecture Optimization v2](Weekly_Business_Report_Workflow_Architecture_Optimization_v2.md)

## v2 Workflow

[Weekly Business Report Workflow v2](workflows/weekly_business_report/WORKFLOW_v2.md)

Phase 1 旧版 `phase1/workflows/weekly_business_report/WORKFLOW.md` 保留，不覆盖。

## Business Asset Initialization

- [执行顺序与 P0 验收标准](Business_Asset_Initialization_Execution_Plan.md)
- [Data Source Inventory 模板](templates/data_source_inventory.template.yaml)
- [正式 Data Source Inventory](assets/data_sources/data_source_inventory.yaml)
- [Future Data Source Backlog 模板](templates/future_data_source_backlog.template.yaml)
- [Dataset Inventory 模板](templates/dataset_inventory.template.yaml)
- [正式 Dataset Inventory](assets/datasets/dataset_inventory.yaml)
- [Dataset Readiness Matrix](assets/datasets/dataset_readiness_matrix.yaml)
- [Dataset标准化与Readiness Gate](Dataset_Standardization_and_Readiness_Gate.md)
- [本地 Discovery 数据边界](discovery/README.md)
- [Pipeline Registry 模板](templates/pipeline_registry.template.yaml)
- [正式 Pipeline Registry](assets/pipelines/pipeline_registry.yaml)
- [Field Mapping 模板](templates/field_mapping.template.yaml)
- [Field Mapping 资产索引](assets/field_mappings/INDEX.md)
- [Field Mapping Readiness Gate](assets/field_mappings/field_mapping_readiness_gate.yaml)
- [Revenue业务线增量Field Mapping Gate](assets/field_mappings/field_mapping_readiness_gate_delta_revenue_business_line.yaml)
- [Field Mapping Initialization 阶段归档](Field_Mapping_Initialization_Archive.md)
- [Business Rule 模板](templates/business_rule.template.yaml)
- [Business Rule 资产索引](assets/business_rules/INDEX.md)
- [Revenue Business Rule Readiness Gate](assets/business_rules/business_rule_readiness_gate_revenue.yaml)
- [Revenue Business Rule Initialization 阶段归档](Business_Rule_Initialization_Revenue_Archive.md)
- [通用查询会话恢复策略](assets/execution_policies/QUERY_SESSION_REFRESH_AND_RETRY_POLICY_V1.yaml)
- `outputs/phase1_5/Business_Asset_Initialization_v2_1.xlsx`

## v2.1 轻量字段更新

- Data Source：增加 `business_purpose`、`criticality`。
- Dataset：增加 `asset_role`、`supported_business_context_candidates`。
- Pipeline：增加 `trigger_type`。
- 其他架构和字段保持 v2 不变。

初始化录入说明：`asset_priority`用于资产初始化排序，与表示运行影响等级的
`criticality`相互独立。

Data Source采用按需激活：Active Inventory只服务当前或已确认近期Workflow。
暂无明确Workflow需求的平台仅进入Future Data Source Backlog，且不能向下建立
Dataset、Pipeline或Metric资产。

## 当前规则

- 不把示例 Pipeline、Dataset、Rule 或 Metric 当作真实业务资产。
- 所有实际业务标识、字段、规则和公式保持 `TBD`。
- Dataset–Pipeline 依赖必须显式配置。
- Pipeline 是运行、验证、异常定位和补跑的最小执行单元。
- Workflow 之间不产生文件级依赖。
- Output Assembly 不执行计算或业务判断。
- Discovery证据、原始邮件、客户/收入明细与生成结果仅保存在本地，不进入Git。
- 禁止Auto-merge；每个PR均需自动检查通过、业务审查完成并由用户明确确认。
