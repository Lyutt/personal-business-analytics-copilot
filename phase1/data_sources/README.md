# Data Source Config

## 目的

数据来源配置只描述“从哪里、何时、按什么规则识别和获取数据”，不保存业务指标公式，也不决定未知字段含义。

## 当前模板

| 文件 | 用途 |
|---|---|
| `data_source_config.template.yaml` | 所有数据源的通用配置结构 |
| `revenue_outlook.template.yaml` | 收入 Outlook 邮件和附件识别 |
| `inventory_platform.template.yaml` | 公司数据平台及导出方式 |

## 盘点原则

- 优先记录当前人工实际使用的来源，不先假设 API。
- 每个来源注明 Owner、权限、更新时间、数据周期和获取方式。
- 文件规则应能得到唯一输入；多个候选时创建异常。
- 认证信息、密码、Token 不写入配置文件。
- 字段 Mapping 不写在 Data Source Config 中，放入对应 Knowledge Pack。
