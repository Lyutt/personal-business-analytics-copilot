# Personal Business Analytics Copilot

## 当前阶段

- 架构方向：已确认。
- Phase 1：已确认。
- 当前阶段：Phase 1.5 — Business Asset Initialization。
- Workflow Architecture Optimization v2：已确认并迁移为独立 v2 文件。
- Data Source、Dataset、Pipeline Registry 与原始范围 Field Mapping：已完成。
- 当前资产类别：Business Rule Initialization（进行中）。
- 当前基线：[Phase 1.5 Baseline Closure — 2026-07-31](phase1_5/BASELINE_CLOSURE_2026-07-31.md)。
- 当前 MVP：Weekly Business Report Workflow。
- 实现状态：尚未进入代码实现。

## 默认读取顺序

1. 当前阶段入口：[phase1_5/INDEX.md](phase1_5/INDEX.md)
2. 已确认的 Phase 1 资产：[phase1/INDEX.md](phase1/INDEX.md)
3. 需要查看整体架构时：[Personal Business Analytics Copilot_架构设计方案_v1.md](Personal%20Business%20Analytics%20Copilot_架构设计方案_v1.md)
4. 只有当前 Workflow 明确引用时，才读取对应 Knowledge Pack、指标或数据源配置。

## 当前硬性边界

- Skill 保持为 Analytics Core、Data Engine、Calculation Engine、Output Engine。
- 具体业务场景通过 Workflow 扩展。
- 邮件仅创建 Outlook Draft，不自动发送。
- 周期、数据源、业务维度、指标、模板和确认规则必须配置化。
- 未知字段、规则冲突、口径冲突不得由 Agent 自行猜测。
- 当前不实现多业务 Workflow、高级洞察或复杂知识图谱。
- Git 仅保存脱敏配置和模板；本地业务数据与生成结果不得上传。
- 禁止 Auto-merge；自动检查通过后仍需用户业务审查并明确确认。
