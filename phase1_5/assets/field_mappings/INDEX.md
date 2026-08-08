# Field Mapping Assets

## 当前状态

- 阶段状态：原始范围已完成；Revenue业务线Sheet增量映射已完成并通过Delta Gate。
- 正式开始日期：2026-07-30。
- 正式完成日期：2026-07-31。
- 已完成Dataset：`DS_REVENUE_SALES_ROLLING_DECK_QTD`、`DS_REVENUE_CTV_EXCL_PLACEMENT_QTD`、`DS_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY`、`DS_INVENTORY_APOLLO_FULL_SITE_STOCK_SUMMARY`、`DS_INVENTORY_APOLLO_PATCH_STOCK_SUMMARY`、`DS_INVENTORY_APOLLO_NON_PATCH_PRODUCT_STOCK_SUMMARY`、`DS_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION`、`DS_NOVABI_PLATFORM_DAU`、`DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS`。
- 当前盘点Dataset：无。
- 当前真实 Mapping Profile 数量：11（已批准11）。
- 阻断问题：0。
- 正式Gate：`field_mapping_readiness_gate.yaml`。
- Customer Revenue Detail复用Rolling Deck两份已批准Mapping与现有本地广告主归属映射；独立Gate为`field_mapping_readiness_gate_customer_revenue_detail.yaml`，不新增或复制客户映射内容。
- Gate后用途变更：`DS_REVENUE_SALES_ROLLING_DECK_QTD`新增“业务线”Sheet，
  作为季度切换首周读取上季度完整业绩收入结果的主来源；增量Field Mapping
  Gate已完成并通过。
- `DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION`保留为备选
  Dataset，不再作为主路径条件必需输入。
- 模板位置：`phase1_5/templates/field_mapping.template.yaml`。
- 本目录只保存经过逐项确认的正式 Mapping Profile。

## 录入原则

- 每个 Dataset 使用独立的 Mapping Profile。
- 只记录原始字段到标准字段的显式映射。
- 不根据字段名称、相似结构或历史结果自动建立映射。
- 未确认字段、类型、业务含义和必填性保持 `TBD`。
- Field Mapping 不包含 Business Rule、Metric Formula 或 Output Mapping。
- Example 不得写入正式资产。

## 初始化进度

正式盘点采用逐个Dataset确认方式。
`DS_REVENUE_SALES_ROLLING_DECK_QTD`的Mapping Profile已于2026-07-30完成确认。
`DS_REVENUE_CTV_EXCL_PLACEMENT_QTD`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_INVENTORY_APOLLO_FULL_SITE_STOCK_SUMMARY`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_INVENTORY_APOLLO_PATCH_STOCK_SUMMARY`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_INVENTORY_APOLLO_NON_PATCH_PRODUCT_STOCK_SUMMARY`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_NOVABI_PLATFORM_DAU`的P0 Mapping Profile已于2026-07-30完成确认。
`DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS`的P0 Mapping Profile已于2026-07-30完成确认。

## Mapping Profile Registry

| Mapping Profile ID | Dataset ID | 状态 |
|---|---|---|
| `MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1` | `DS_REVENUE_SALES_ROLLING_DECK_QTD` | Approved v1.0.0 |
| `MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1` | `DS_REVENUE_SALES_ROLLING_DECK_QTD` | Approved v1.0.0 |
| `MAP_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_BUSINESS_LINE_V1` | `DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION` | Approved v1.0.0 |
| `MAP_REVENUE_CTV_EXCL_PLACEMENT_QTD_V1` | `DS_REVENUE_CTV_EXCL_PLACEMENT_QTD` | Approved v1.0.0 |
| `MAP_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY_V1` | `DS_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY` | Approved v1.0.0 |
| `MAP_INVENTORY_APOLLO_FULL_SITE_STOCK_SUMMARY_V1` | `DS_INVENTORY_APOLLO_FULL_SITE_STOCK_SUMMARY` | Approved v1.0.0 |
| `MAP_INVENTORY_APOLLO_PATCH_STOCK_SUMMARY_V1` | `DS_INVENTORY_APOLLO_PATCH_STOCK_SUMMARY` | Approved v1.0.0 |
| `MAP_INVENTORY_APOLLO_NON_PATCH_PRODUCT_STOCK_SUMMARY_V1` | `DS_INVENTORY_APOLLO_NON_PATCH_PRODUCT_STOCK_SUMMARY` | Approved v1.0.0 |
| `MAP_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION_V1` | `DS_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION` | Approved v1.0.0 |
| `MAP_USER_ANALYTICS_NOVABI_PLATFORM_DAU_V1` | `DS_NOVABI_PLATFORM_DAU` | Approved v1.0.0 |
| `MAP_ADVERTISING_APOLLO_PRODUCT_CUSTOMER_DELIVERY_CHANGE_V1` | `DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS` | Approved v1.0.0 |

## 阶段归档

- Readiness Gate：Passed。
- 归档记录：`phase1_5/Field_Mapping_Initialization_Archive.md`。
- 历史下一阶段：Business Rule Initialization（已完成）。当前项目状态以统一Status Index为准。
- 本阶段结束不代表进入代码实现。

## Gate后增量

- 原Gate保留为当时9个Weekly范围Dataset的有效审查记录。
- 增量对象是现有`DS_REVENUE_SALES_ROLLING_DECK_QTD`中的“业务线”Sheet，
  不新建Dataset，也不覆盖已批准的明细Sheet Mapping Profile。
- 增量已新增两个独立Sheet级Mapping Profile，分别对应业务线主来源和季度结算备选来源。
- 正式Delta Gate：`field_mapping_readiness_gate_delta_revenue_business_line.yaml`。
