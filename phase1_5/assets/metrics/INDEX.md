# Metric Library 资产索引

## 当前状态

- Workflow：`WF_WEEKLY_BUSINESS_REPORT`
- 阶段状态：Completed
- Readiness Gate：Passed
- 完成日期：2026-08-02
- 代码实现：未开始
- 实现入口：由`GATE_CODE_IMPLEMENTATION_WF_WEEKLY_BUSINESS_REPORT_V1`统一控制

## Metric Library

| Library | 覆盖范围 | 状态 |
|---|---|---|
| `METRIC_LIBRARY_REVENUE_WEEKLY_V1` | 技术线、CTV、智能音箱、极速版收入 | Approved |
| `METRIC_LIBRARY_INVENTORY_WEEKLY_V1` | 全站、贴片、非贴片产品、品牌时刻库存及售卖率，广告曝光支持指标 | Approved |
| `METRIC_LIBRARY_USER_ANALYTICS_DAU_V1` | 全平台分日DAU、周均DAU及环比 | Approved |
| `METRIC_LIBRARY_CUSTOMER_REVENUE_DETAIL_V1` | 分客户技术线收入、预估完成与同比、周新增 | Approved design only |

## 结构统计

- 稳定Metric概念：9个。
- Metric Variant：48个。
- Pipeline显式Variant绑定：48个。
- 条件分析策略：1个。
- 重复Metric ID：0。
- 重复Metric Variant ID：0。
- 缺失或孤立Variant：0。

Customer Revenue Detail独立增量：4个稳定Metric概念、14个Metric Variant、14个显式Result Contract绑定；不改变Weekly冻结的48个Variant基线。

## 正式Gate

[Metric Library Readiness Gate](metric_library_readiness_gate.yaml)

[Customer Revenue Detail Metric Gate](metric_library_readiness_gate_customer_revenue_detail.yaml)

本Gate只证明Metric资产可进入后续阶段，不单独授予代码实现权限。

## 约束

- Metric表示稳定业务概念；Metric Variant承载Dataset、Business Context、Rule和Formula的具体实现。
- Pipeline不得按字段、名称或历史相似性自动选择Metric Variant。
- 本地产品筛选、资源条件和客户结果只通过本地Knowledge Pack引用，不复制到可提交资产。
- Output Assembly不执行计算、规则判断或异常触发。
- 未确认业务内容保持`TBD`。
