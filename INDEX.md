# Personal Business Analytics Copilot

## 当前阶段

- 架构方向：已确认。
- Phase 1：已确认。
- 当前阶段：Code Implementation Readiness Review 已完成。
- Workflow Architecture Optimization v2：已确认并迁移为独立 v2 文件。
- Weekly Workflow的Data Source、Dataset、Pipeline、Field Mapping、Business Rule、Metric Library、Metric Store逻辑契约与Output Mapping：已完成P0资产收口。
- 统一状态入口：[Phase 1.5 Status Index](phase1_5/assets/readiness/status_index.yaml)。
- 最终Gate：[Code Implementation Readiness Gate](phase1_5/assets/readiness/code_implementation_readiness_gate.yaml)。
- 当前 MVP：Weekly Business Report Workflow。
- 实现状态：尚未开始；允许启动代码实现，但端到端运行验收仍需完成3个本地Metric Store Runtime Bootstrap。

## 默认读取顺序

1. 统一状态入口：[Phase 1.5 Status Index](phase1_5/assets/readiness/status_index.yaml)
2. 当前阶段入口：[phase1_5/INDEX.md](phase1_5/INDEX.md)
3. 已确认的 Phase 1 资产：[phase1/INDEX.md](phase1/INDEX.md)
4. 需要查看整体架构时：[Personal Business Analytics Copilot_架构设计方案_v1.md](Personal%20Business%20Analytics%20Copilot_架构设计方案_v1.md)
5. 只有当前 Workflow 明确引用时，才读取对应 Knowledge Pack、指标或数据源配置。

## 当前硬性边界

- Skill 保持为 Analytics Core、Data Engine、Calculation Engine、Output Engine。
- 具体业务场景通过 Workflow 扩展。
- 邮件仅创建 Outlook Draft，不自动发送。
- 周期、数据源、业务维度、指标、模板和确认规则必须配置化。
- 未知字段、规则冲突、口径冲突不得由 Agent 自行猜测。
- 当前不实现多业务 Workflow、高级洞察或复杂知识图谱。
- Git 仅保存脱敏配置和模板；本地业务数据与生成结果不得上传。
- 禁止 Auto-merge；自动检查通过后仍需用户业务审查并明确确认。
