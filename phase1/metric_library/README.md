# Metric Library v0.1 — Design Template

## 1. 目的

Metric Library 是所有指标定义的唯一正式来源。Weekly Workflow 和 Knowledge Pack 只能引用 `Metric_ID`，不能复制公式。

当前阶段不预设收入或库存指标口径。先将现有周报指标逐项导入，记录冲突，再由 Owner 批准。

## 2. 指标选择原则

Weekly Business Report 的第一批指标应优先满足：

- 直接支持每周经营判断。
- 在周频上有足够信号，不是只适合季度或年度观察。
- 计算输入可以稳定获取。
- 计算逻辑能够确定执行，不依赖每周临时解释。
- 有明确 Owner 和人工验证方式。
- 不易通过遗漏维度或改变口径制造“虚假改善”。

MVP 不追求指标数量。先建立能够支撑收入、库存和周报比较的最小指标集。

## 3. 指标分层

| 层级 | 含义 | 示例性质 |
|---|---|---|
| Base | 直接从标准化字段计算 | 金额、数量 |
| Aggregate | 按周期和维度汇总 | 客户/资源维度汇总 |
| Derived | 由其他指标计算 | 占比、达成率 |
| Comparison | 跨周期比较 | WoW 差额、WoW 变化率 |
| Presentation | 单位和展示格式 | 万元、百分比 |

展示换算不能改变底层指标值；Comparison Metric 必须明确基期。

## 4. 单指标必要字段

- Identity：Metric_ID、名称、版本、状态。
- Meaning：业务定义、包含/排除项、用途。
- Formula：公式、输入字段/上游指标、缺失值规则。
- Grain：最细粒度、允许汇总维度。
- Time：支持周期、日期字段、比较方式。
- Ownership：业务 Owner、技术/数据 Owner。
- Validation：对账来源、容差、边界规则。
- Governance：生效日期、是否可与旧版本比较、变更原因。
- Output：单位、精度、展示位置。

## 5. 状态

`Draft → In Review → Active → Deprecated`

- 只有 Active 指标可用于正式输出。
- Draft 指标可以用于历史回放，但必须显式标识。
- 指标口径冲突时，不允许默认为“最新文件中的公式”。

## 6. 版本

- Patch：说明、Owner 或展示格式修改，业务含义不变。
- Minor：计算规则改变但仍可解释比较。
- Major：口径不可直接比较。
- 每个版本有生效日期；历史是否回算单独决定。

## 7. Phase 1 盘点步骤

1. 从现有周报、收入 Excel、人工核对表提取指标名称。
2. 去重但不合并口径冲突。
3. 补充公式、输入、粒度、周期和 Owner。
4. 标记该指标是否为周报必需。
5. 定义验证方式和容差。
6. 用至少两个历史周期人工复算。
7. 批准后转为 Active。

## 8. 维护载体

- 批量盘点：`Business_Asset_Discovery_Template.xlsx` 的“指标登记”。
- 单个正式指标：`metric_definition.template.yaml` 复制后形成独立定义文件。
