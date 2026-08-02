# Inventory Knowledge Pack — Draft

> 历史状态说明：本目录保留Phase 1盘点模板，不代表当前Weekly Workflow准备度。
> 已确认的库存资产以`phase1_5/assets/`和统一Status Index为准。

## 目的

保存 Weekly Business Report 所需的已确认库存业务知识。库存数据获取步骤属于 Workflow，通用字段处理属于 Data Engine。

## 当前文件

| 文件 | 用途 | 当前状态 |
|---|---|---|
| `business_definition.template.md` | 库存范围、术语、粒度 | 待填写 |
| `dimensions.template.yaml` | Inventory 标准业务维度 | 待填写 |
| `business_rules.template.yaml` | 库存业务规则 | 待填写 |
| `validation_rules.template.yaml` | 库存数据和指标校验 | 待填写 |
| `metric_manifest.template.yaml` | 本 Pack 引用的 Metric_ID | 待填写 |
| `../../data_sources/inventory_platform.template.yaml` | 公司平台来源 | 待填写 |
| Phase 1 盘点工作簿“Inventory字段映射” | 字段 Mapping | 待填写 |

## 建议盘点顺序

1. 提供平台访问/导出说明和至少两个周的导出样例。
2. 确认库存记录粒度和快照时间。
3. 定义产品/资源及其他业务维度。
4. 建立字段 Mapping 和去重键。
5. 记录负数、零值、失效资源、重复资源等处理规则。
6. 确认库存指标和人工对账方式。

## 发布门槛

- 库存快照时间、粒度和唯一键明确。
- 所有必需字段已映射。
- 核心库存指标有验证规则。
- 平台导出时间和数据新鲜度规则明确。
