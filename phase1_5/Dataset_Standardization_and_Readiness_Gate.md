# Dataset Standardization and Readiness Gate

## 目标

在进入Pipeline Registry前，区分Dataset边界确认与运行就绪状态，并对当前
Dataset Inventory进行轻量标准化。

本阶段不定义Field Mapping、Business Rule、Metric或Pipeline。

## 逐问流程

1. 逐个确认Dataset在Weekly Business Report中的使用角色。
2. 只对Required、Optional及Diagnostic Dataset识别运行阻断TBD。
3. 完成不改变业务含义的配置类型标准化。
4. 逐个形成Pipeline Registry准入结论。

每次只确认一个问题。未经确认的业务信息保持`TBD`。

## 状态含义

- `registration_status: confirmed`：Dataset身份和边界已确认。
- `readiness.status: not_assessed`：尚未检查运行所需信息。
- `readiness.status: blocked`：存在明确阻断项。
- `readiness.status: conditional`：允许在明确人工条件下使用。
- `readiness.status: ready`：当前范围内不存在已知阻断项。
- `pipeline_registry_eligible: allow`：允许进入Pipeline Registry。
- `pipeline_registry_eligible: hold`：暂不进入Pipeline Registry。

## 标准化范围

- `business_domains`统一为列表。
- 获取方式及结果格式统一为列表。
- 查询参数统一为对象列表。
- 更新频率分别使用`source_data_refresh_frequency`、
  `dataset_update_frequency`、`planned_acquisition_frequency`与
  `query_execution_frequency`表达，不再使用语义模糊的
  `update_frequency`。
- `approval_status`只表示边界确认。
- 运行就绪度只在Readiness Matrix中维护。
- Workflow使用关系采用独立矩阵及后续Pipeline Registry，不写死在Dataset中。

## 本轮结果

- 已审查Dataset：10个。
- `pipeline_registry_eligible: allow`：9个。
- `pipeline_registry_eligible: hold`：1个。
- 当前Weekly Business Report的阻塞性Readiness TBD：0个。
- 季度结算收入确认Dataset因不属于当前周报范围保持`hold`。
- 分广告产品客户投放变化分析Dataset已通过条件性Pipeline与显式Trigger
  Contract完成准入；正式Rule ID和产品覆盖规则延后到Business Rules阶段，
  不阻塞Field Mapping。

`allow`表示Dataset具备进入后续资产初始化的最低条件，不表示Field Mapping、
Business Rule、Metric或运行实现已经完成。Weekly Workflow Pipeline Registry
已于2026-07-29完成，最新状态以正式Pipeline Registry为准。
