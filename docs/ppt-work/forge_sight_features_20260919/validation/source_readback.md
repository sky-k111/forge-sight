# source

- Source: `source.pptx`
- Total slides: 3

## Slide 1

![Slide 1 Image 1](source_readback_files/image5.png)

![Slide 1 Image 2](source_readback_files/image2.png)

ForgeSight

背景与目标

产品服务

产品特色

技术路线

团队合作展示

成本模型和可行性分析

3.2

智能异常监控

业务服务

异常生命周期（趋势示意）

监控值

连续监测数据断流、工序良率下降和非计划停机。

异常出现后通知责任人，呈现趋势、样本量、

数据截至时间与恢复状态。

规则阈值

底层实现

规则绑定：从受控模板选字段，验证后发布。

1

正常

触发

持续

恢复

周期评估：只读查询，以确定性规则判断。

2

规则与字段绑定

周期只读评估

告警与通知

状态与投递：留存证据，记录触发、持续、

3

恢复，并通过站内消息或 IM 通知。

触发判断由固定统计规则完成，LLM 不决定是否告警。

示意曲线不代表真实生产数据。

从一次性查询延伸到持续监测，让异常与恢复都有据可查。

## Slide 2

![Slide 2 Image 3](source_readback_files/image5.png)

![Slide 2 Image 4](source_readback_files/image2.png)

ForgeSight

背景与目标

产品服务

产品特色

技术路线

团队合作展示

成本模型和可行性分析

3.4

多数据源适配

业务服务

统一接入与安全执行

企业保留原有数据库，在同一平台登记、扫描、

MySQL

治理和问析，无需先集中搬迁业务数据。

统一资源目录

PostgreSQL

Schema / 表 / 字段

Adapter

MariaDB

Registry

底层实现

只读安全网关

适配器注册：统一连接、扫描、方言与执行接口。

SQL Server · beta

1

AST / 范围 / 成本

元数据归一：按租户、数据源和 Schema 隔离。

2

Oracle · beta

Agent 问析

安全执行：方言感知 SQL 经只读校验、

3

表白名单、限行、超时与成本门控。

SQL Server、Oracle 为 beta 适配器；只读权限由数据库账号保证。

保留原有数据底座，扩展数据库类型时复用同一问析流程。

## Slide 3

![Slide 3 Image 5](source_readback_files/image5.png)

![Slide 3 Image 6](source_readback_files/image2.png)

ForgeSight

背景与目标

产品服务

产品特色

技术路线

团队合作展示

成本模型和可行性分析

3.5

受治理技能学习

业务服务

运行经验到下一次问析

把成功分析中的方法沉淀为技能，经审核发布后，

在后续问题中检索复用，持续吸收使用反馈。

分析 Run

用户反馈

候选技能

底层实现

经验提取：有帮助或修正反馈形成候选技能。

1

检索复用

发布与索引

人工审核

人工治理：审核、发布或停用，保留版本与证据。

2

检索复用：词法与语义召回已发布技能，

3

命中记录回流到下一轮学习。

技能只优化分析方法，不扩大用户的数据访问权限。

每次查询仍受数据范围、表白名单和 SQL 安全校验约束。

让有效分析方法持续积累，同时保留审核与追溯。
