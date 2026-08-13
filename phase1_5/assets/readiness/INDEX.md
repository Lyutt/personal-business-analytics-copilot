# Readiness and Implementation Entry

## 当前统一状态

- Workflow：`WF_WEEKLY_BUSINESS_REPORT`
- Phase 1.5 P0 Business Asset Initialization：已完成。
- Phase 1.5 Final Acceptance已通过；Weekly与Customer Implementation Baseline 1.0.0继续作为冻结历史基线，不因后续实现被追溯改写。
- Weekly Stage 2 Acquisition Runtime Foundation已完成并在PR #8合并；Pydantic V2 parity与Ruff Phase 1、2A、2B已在PR #9–#12合并。
- Stage 2.5仅同步治理与实施边界。下一候选阶段为Data Engine / Business Execution，尚未启动并需要Owner独立授权。
- 允许端到端 MVP 运行验收：暂为 Conditional。
- Customer Revenue Detail Workflow：设计资产已完成并形成独立Baseline；未获得代码实现授权，且不阻断Weekly Workflow。
- Provider capability validation、真实数据计算与Runtime Acceptance均未开始；Scheduler和Automatic Draft未授权，`auto_send=false`。

## 唯一状态入口

- [统一状态索引](status_index.yaml)
- [Code Implementation Readiness Gate](code_implementation_readiness_gate.yaml)
- [Implementation Baseline 1.0.0](implementation_baseline.yaml)
- [Customer Revenue Detail Status Index](status_index_customer_revenue_detail.yaml)
- [Customer Revenue Detail Code Gate](code_implementation_readiness_gate_customer_revenue_detail.yaml)
- [Customer Revenue Detail Implementation Baseline 1.0.0](implementation_baseline_customer_revenue_detail.yaml)
- [Inventory / Advertising Policy Gate](../policies/inventory_advertising_policy_readiness_gate.yaml)
- [Metric Result Store Readiness Matrix](../metric_stores/metric_result_store_readiness_matrix.yaml)

Code Implementation Readiness Gate是pre-Stage 2历史审查记录；其他阶段Gate也只证明各自资产阶段通过，不单独授予后续阶段权限。

## Runtime Bootstrap 阻断项

以下 Store 的逻辑契约、共享SQLite Schema与幂等键已确认，但本地路径、SQLite文件和初始表尚未初始化：

- `STORE_WEEKLY_INVENTORY_HISTORICAL`
- `STORE_WEEKLY_USER_ANALYTICS_HISTORICAL`
- `STORE_WEEKLY_ADVERTISING_HISTORICAL`

这些项目不阻断代码实现启动，但会阻断端到端 MVP Runtime Acceptance。
`configured_display_values`与`metric_results`共用同一SQLite文件，但前者不是Metric Result表。
所有真实路径、账户、模板、收件人和运行数据继续保持本地，不进入 Git。
