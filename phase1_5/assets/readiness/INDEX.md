# Readiness and Implementation Entry

## 当前统一状态

- Workflow：`WF_WEEKLY_BUSINESS_REPORT`
- Phase 1.5 P0 Business Asset Initialization：已完成。
- Phase 1.5 Final Acceptance已通过；Weekly与Customer Implementation Baseline 1.0.0继续作为冻结历史基线，不因后续实现被追溯改写。
- Weekly Stage 2 Acquisition Runtime Foundation已完成并在PR #8合并；Pydantic V2 parity与Ruff Phase 1、2A、2B已在PR #9–#12合并；Stage 2.5治理同步已在PR #13合并。
- Stage 3A CTV vertical slice已在PR #14完成并合并；CTV prior-year source authority修正已在PR #15合并，专项测试47/47 PASS。
- Runtime Contract v1.2仅为active execution/validation candidate，未晋级为新的正式Baseline。Stage 3B Revenue Expansion已完成implementation并通过exit qualification；Stage 3C已在PR #18完成其余8条Weekly executor与7个共享SQLite Store Asset最小实现，并按exact merged diff完成追溯治理登记，且不追溯声称事前scope authorization；Stage 3D deterministic sequential Weekly Workflow Runner已完成implementation并通过exit qualification；Stage 3E `Weekly Output Assembly and Review Preview` exact scope已获得Owner确认，但implementation未授权。
- 允许端到端 MVP 运行验收：暂为 Conditional。
- Customer Revenue Detail Workflow：设计资产已完成并形成独立Baseline；未获得代码实现授权，且不阻断Weekly Workflow。
- Runtime Acceptance、Stage 3E implementation、Stage 3F、Provider、Scheduler/Queue、Outlook Draft/Send、Cutover与Baseline promotion/refreeze均未授权，`auto_send=false`。

## 唯一状态入口

- [统一状态索引](status_index.yaml)
- [Stage 3A CTV Local Real-Data Calculation Qualification Status](stage3a_ctv_qualification_status.yaml)
- [Stage 3E Weekly Output Assembly and Review Preview Exact Scope](stage3e_weekly_output_assembly_review_preview_exact_scope.yaml)
- [Stage 3D Weekly Workflow Runner Exact Scope](stage3d_weekly_workflow_runner_exact_scope.yaml)
- [Stage 3C Weekly Executor Completion Retrospective Scope](stage3c_weekly_executor_completion_retrospective_scope.yaml)
- [Stage 3B Revenue Expansion Exact Scope Contract](stage3b_revenue_expansion_exact_scope.yaml)
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
