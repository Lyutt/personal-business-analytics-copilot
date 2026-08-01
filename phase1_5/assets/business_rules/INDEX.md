# Business Rule Assets

## 当前状态

- 阶段状态：进行中。
- 正式开始日期：2026-07-31。
- 本轮恢复日期：2026-08-01。
- 当前Workflow：`WF_WEEKLY_BUSINESS_REPORT`。
- 当前规则类别：Revenue分类、过滤与季度上下文规则。
- 已批准Rule数量：3。
- 代码实现：未开始。

## 本轮恢复检查点

- 从已完成安全收口的`main`重新进入Business Rule Initialization。
- 当前继续处理季度切换首周的上季度完整收入结果来源选择规则。
- 已确认主来源为`DS_REVENUE_SALES_ROLLING_DECK_QTD`工作簿中的
  “业务线”Sheet。
- 已确认`DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION`
  仅作为备选来源。
- 备选来源启用条件、主来源增量Field Mapping及异常处置仍保持`TBD`，
  未经Owner确认不得生成正式Rule。
- 本阶段继续采用逐批提问确认，不自动进入Metric Library或下一资产阶段。

## 初始化原则

- Rule必须引用显式Dataset、标准字段、Context或Result Contract。
- 不根据字段名称、相似性或历史结果自动补全规则。
- 未确认条件、优先级、异常分支与适用范围保持`TBD`。
- Business Rule不定义Metric公式。
- Business Rule不承担Output格式和文案组装。
- 通用规则可以被多个Pipeline复用，不按报告类型拆成Skill。

## P0建议顺序

1. Business Calendar & Report Mode。
2. Revenue分类、过滤与季度上下文规则。
3. Inventory产品路由与库存业务上下文规则。
4. 售卖率异常触发规则。
5. 客户投放变化筛选与排序规则。

每个规则类别完成Owner确认后再进入下一类。

## 当前依赖缺口

- 季度切换首周的技术线与CTV上季度完整结果，主来源调整为
  `DS_REVENUE_SALES_ROLLING_DECK_QTD`工作簿中的“业务线”Sheet。
- `DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION`仅作为备选
  来源，其启用条件尚待Owner确认。
- 在“业务线”Sheet增量Field Mapping完成前，上季度结果资格Rule不得批准。

## Rule Registry

| Rule ID | 规则名称 | 状态 |
|---|---|---|
| `BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1` | Weekly收入模块常规周与季度切换首周判定 | Approved v1.0.0 |
| `BR_REVENUE_TECHNICAL_SINGLE_COUNT_ELIGIBILITY_V1` | 技术线单计硬广收入记录纳入规则 | Approved v1.0.0 |
| `BR_REVENUE_QTD_HISTORY_CARRY_FORWARD_ELIGIBILITY_V1` | 细分业务线QTD历史累计承接资格规则 | Approved v1.0.0 |

## 已确认无需额外Rule的Pipeline

| Pipeline ID | 决策 |
|---|---|
| `PL_REVENUE_CTV_WEEKLY` | Field Mapping完成后不做字段分类或记录筛选，全部有效记录进入Metric阶段 |
| `PL_REVENUE_SMART_SPEAKER_WEEKLY` | 不增加查询模板之外的业务线筛选 |
| `PL_REVENUE_FAST_VERSION_WEEKLY` | 不增加查询模板之外的业务线筛选 |
