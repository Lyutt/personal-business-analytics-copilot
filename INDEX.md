# Personal Business Analytics Copilot

## 当前阶段

- 2026-08-09 设计资产收口以远端 `main` 的 `fb3a4b7` 为 review base；Baseline以 merge-neutral 的冻结时历史血缘记录候选分支及验证目标 commit/tree，不把当前是否已合并作为持续状态。
- Active 占位项只允许分类为 `blocking`、`runtime-only`、`not-required-for-MVP` 或 `superseded`；未分类项阻断受影响范围，禁止推断业务值。
- Weekly 与 Customer 可复用已登记输入资产，但彼此不产生文件、输出、Result Contract、Metric Store、Baseline 或 Gate 依赖。
- 架构方向：已确认。
- Phase 1：已确认。
- 当前正式阶段：`Stage 3D Weekly Workflow Runner Implementation Completed - Exit Qualified; Later Stages Not Authorized`。
- Stage 2 已在 PR #8 完成并合并；Pydantic V2 parity 与 Ruff Phase 1、2A、2B 已在 PR #9–#12 完成并合并；Stage 2.5 治理同步已在 PR #13 合并。
- Stage 3A CTV vertical slice 已在 PR #14 完成并合并；CTV prior-year source authority 修正已在 PR #15 合并，Stage 3A CTV 专项测试 47/47 PASS。
- Weekly Business Report Baseline 1.0.0与Customer Revenue Detail Baseline 1.0.0继续作为各自冻结历史基线，不因后续实现被追溯改写。
- Workflow Architecture Optimization v2：已确认并迁移为独立 v2 文件。
- Weekly Workflow的Data Source、Dataset、Pipeline、Field Mapping、Business Rule、Metric Library、Result Contract、Metric Store逻辑契约与Output Mapping：已完成P0资产收口。
- Result Contract入口：[Result Contract资产索引](phase1_5/assets/result_contracts/INDEX.md)；[Result Contract Readiness Gate](phase1_5/assets/result_contracts/result_contract_readiness_gate.yaml)已通过。
- 本地External Asset仅登记版本合同与`LOCAL_ONLY`运行时占位符，真实版本和内容指纹不得进入Git。
- Weekly状态入口：[Phase 1.5 Weekly Status Index](phase1_5/assets/readiness/status_index.yaml)；Customer Revenue Detail状态入口：[Customer Status Index](phase1_5/assets/readiness/status_index_customer_revenue_detail.yaml)。
- 历史实现入口Gate：[Code Implementation Readiness Gate](phase1_5/assets/readiness/code_implementation_readiness_gate.yaml)；其记录 pre-Stage 2 审查结论，不代表当前阻断状态。
- 当前已完成设计资产的Workflow：Weekly Business Report与独立的Customer Revenue Detail；Weekly 已完成 Acquisition Runtime Foundation 与 Stage 3A CTV vertical slice，Customer 仍保持独立未授权状态。
- Runtime Contract v1.2 当前仅为 active execution/validation candidate，尚未晋级，也未重冻结为新的正式 Baseline。
- Stage 3B Revenue Expansion 已完成 implementation 并通过 exit qualification。Technical 本地真实数据 calculation qualification 为 PASS；CTV 复用 Stage 3A PASS evidence；Smart Speaker / Fast Version real-data Source-to-Result qualification 未执行，且不属于 Stage 3B exit requirement。
- Stage 3C 已在 PR #18 完成其余 8 条 Weekly executor 与 7 个共享 SQLite Store Asset 最小实现；当前登记为基于 exact merged diff 的追溯治理对账，不追溯声称事前 scope authorization。
- Stage 3D deterministic sequential Weekly Workflow Runner 已完成 implementation 与 exit qualification；复用既有 12 条 executor，并保持显式 activation、dependency、Result Contract handoff 与 failure-isolation 边界。
- Runtime Acceptance、Stage 3E、Stage 3F、Provider、Scheduler、Draft/Send、Cutover 与 Baseline promotion/refreeze 均未授权，`auto_send=false`。

## 默认读取顺序

1. 统一状态入口：[Phase 1.5 Status Index](phase1_5/assets/readiness/status_index.yaml)
2. 当前阶段入口：[phase1_5/INDEX.md](phase1_5/INDEX.md)
3. 冻结实现基线：[Implementation Baseline](phase1_5/assets/readiness/implementation_baseline.yaml)
4. 已确认的 Phase 1 历史资产：[phase1/INDEX.md](phase1/INDEX.md)
5. 需要查看整体架构时：[Personal Business Analytics Copilot_架构设计方案_v1.md](Personal%20Business%20Analytics%20Copilot_架构设计方案_v1.md)
6. 只有当前 Workflow 明确引用时，才读取对应 Knowledge Pack、指标或数据源配置。

## 当前硬性边界

- PBAC Contract 决定“什么是正确的”，第三方库只负责“更省代码地执行和验证这个正确性”。任何第三方库均不得成为或改变 PBAC 业务合同、规则、指标、Workflow、Manifest、Runtime 状态及完成语义的权威。
- Skill 保持为 Analytics Core、Data Engine、Calculation Engine、Output Engine。
- 具体业务场景通过 Workflow 扩展。
- 邮件仅创建 Outlook Draft，不自动发送。
- 周期、数据源、业务维度、指标、模板和确认规则必须配置化。
- 未知字段、规则冲突、口径冲突不得由 Agent 自行猜测。
- 当前允许多个独立Workflow的设计资产；不因设计完成而实现多Workflow运行、高级洞察或复杂知识图谱。
- Git 仅保存脱敏配置和模板；本地业务数据与生成结果不得上传。
- 禁止 Auto-merge；自动检查通过后仍需用户业务审查并明确确认。
