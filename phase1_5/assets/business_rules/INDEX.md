# Business Rule Assets

## 当前状态

- 阶段状态：Weekly Workflow P0 Business Rule范围已完成并通过Gate。
- 正式开始日期：2026-07-31。
- 本轮恢复日期：2026-08-01。
- Weekly原范围保持冻结；`WF_CUSTOMER_REVENUE_DETAIL`独立复用两条Revenue规则，并由`POLICY_CUSTOMER_REVENUE_DETAIL_V1`承载其已确认的客户集合、排序、Top20、校验和输出边界。
- Revenue规则状态：已完成Owner确认与Readiness Gate。
- 已批准Revenue Rule数量：6。
- 已确认但待Field Mapping的Revenue Rule数量：0。
- Revenue Rule未确认业务决策数量：0。
- 代码实现：未开始；是否进入实现由最终Code Implementation Readiness Gate统一决定。

## Revenue收口结果

- 从已完成安全收口的`main`重新进入Business Rule Initialization。
- 季度切换首周的上季度完整收入结果来源选择规则已确认。
- 已确认主来源为`DS_REVENUE_SALES_ROLLING_DECK_QTD`工作簿中的
  “业务线”Sheet。
- 已确认`DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION`
  仅作为备选来源。
- 备选版本按终版、初版、其他确认版本排序；同类版本选发送时间最新者。
- 备选结果为空、非数字、0或负数时判定失败。
- 技术线与CTV原则上来源可用性一致；如出现不一致，允许按业务线独立选择
  来源，并在最终周报输出时提醒Owner。
- Rolling Deck周度邮件与季度结算邮件以正文语义为主分类，日期为辅助校验。
- 2026年技术线与CTV去年同期文件都使用“向前一年再加一天”匹配；
  2027年及以后必须重新确认。
- 主来源业务线Sheet与备选业绩-业务线Sheet的增量Field Mapping已通过Delta Gate。
- Revenue规则Gate只负责本阶段收口，不单独授权代码实现；当前项目状态以统一Status Index为准。

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

## 已解决依赖

- 季度切换首周的技术线与CTV上季度完整结果，主来源调整为
  `DS_REVENUE_SALES_ROLLING_DECK_QTD`工作簿中的“业务线”Sheet。
- `DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION`仅作为备选
  来源，其启用条件已由Owner确认。
- 两条季度切换/同期来源规则的Field Mapping依赖已解决，并已转为Approved v1.0.0。

## Rule Registry

| Rule ID | 规则名称 | 状态 |
|---|---|---|
| `BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1` | Weekly收入模块常规周与季度切换首周判定 | Approved v1.0.0 |
| `BR_REVENUE_TECHNICAL_SINGLE_COUNT_ELIGIBILITY_V1` | 技术线单计硬广收入记录纳入规则 | Approved v1.0.0 |
| `BR_REVENUE_QTD_HISTORY_CARRY_FORWARD_ELIGIBILITY_V1` | 细分业务线QTD历史累计承接资格规则 | Approved v1.0.0 |
| `BR_REVENUE_ROLLING_DECK_EMAIL_CLASSIFICATION_V1` | Rolling Deck周度与季度结算邮件分类 | Approved v1.0.0 |
| `BR_REVENUE_PREVIOUS_QUARTER_RESULT_SOURCE_SELECTION_V1` | 季度切换首周上季度完整收入结果来源选择 | Approved v1.0.0 |
| `BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1` | 2026年技术线与CTV同期收入文件匹配 | Approved v1.0.0 |

## 通用执行策略

- `QUERY_SESSION_REFRESH_AND_RETRY_POLICY_V1`：已批准；仅显式绑定的Apollo和NovaBI
  Pipeline可在会话失效时刷新一次、恢复并验证查询配置后重试一次。
- `POLICY_CUSTOMER_REVENUE_DETAIL_V1`：客户Workflow专用；邮件未到时每30分钟持续复查，且不改变Weekly的一次重试策略。

## 已确认无需额外Rule的Pipeline

| Pipeline ID | 决策 |
|---|---|
| `PL_REVENUE_CTV_WEEKLY` | Field Mapping完成后不做字段分类或记录筛选，全部有效记录进入Metric阶段 |
| `PL_REVENUE_SMART_SPEAKER_WEEKLY` | 不增加查询模板之外的业务线筛选 |
| `PL_REVENUE_FAST_VERSION_WEEKLY` | 不增加查询模板之外的业务线筛选 |
