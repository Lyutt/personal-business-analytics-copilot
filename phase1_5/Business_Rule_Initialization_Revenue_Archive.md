# Revenue Business Rule Initialization Archive

## 阶段结果

- Workflow：`WF_WEEKLY_BUSINESS_REPORT`
- Business Domain：Revenue
- Owner确认完成日期：2026-08-01
- Revenue Rule数量：6
- Approved：4
- Confirmed / Pending Field Mapping：2
- 未确认业务决策：0
- 代码实现：未开始

## 已收口规则范围

1. 收入模块常规周与季度切换首周模式判定。
2. 技术线单计硬广有效记录集。
3. 智能音箱与极速版QTD历史结果承接资格。
4. Rolling Deck周度邮件与季度结算邮件分类。
5. 季度切换首周上季度完整收入的主来源和备选来源选择。
6. 2026年技术线与CTV去年同期Rolling Deck文件匹配。

## 已批准的共享执行策略

Apollo和NovaBI页面会话失效时，显式绑定的Pipeline允许刷新一次，
重新进入查询模块、选择模板、恢复并验证参数后重试一次。
禁止复用刷新前页面结果，禁止自动填写凭据。

## 已知依赖

以下两条规则的业务逻辑已确认，但在增量Field Mapping通过前不得转为Approved：

- 季度切换首周上季度完整收入来源选择。
- 2026年去年同期收入文件匹配。

## 阶段边界

- 收入金额、QTD、同比与环比公式进入Metric Library。
- 周报展示文案和首周特殊样式进入Output Mapping。
- 库存和DAU的去年同期日期仅“年度向前一年”，留待对应Domain初始化。
- 本次收口不自动进入下一阶段。
