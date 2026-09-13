# AIRP 24 题扩展 Agent 实验结果

## 实验设计

使用 `gpt-5.6-luna`、low reasoning，对 24 个真实源码问题分别执行 baseline 和 premodel，共 48 次模型调用。两组使用同一源码快照，任务顺序交叉；baseline 最多执行三次源码读取，premodel 在第一次模型请求中接收 AIRP Hook 的真实输出。索引时间计入延迟，Hook 文本计入输入 Token。

原始结果：[premodel-hook-v17.json](../benchmarks/runs/premodel-hook-v17.json)。24 个逐题检查点保存在 `benchmarks/runs/premodel-hook-v17.parts/`。

## 全量结果（修复前）

| 指标 | baseline | AIRP premodel | 变化 |
|---|---:|---:|---:|
| 总模型 Token | 835,836 | 342,218 | **−59.06%** |
| 输入 Token | 830,912 | 341,205 | −58.94% |
| 严格格式正确 | 21/24 | 20/24 | −4.17 pp |
| 源码命令调用 | 31 | 0 | **−100%** |
| 总耗时 | 524.80 s | 229.81 s | **−56.21%** |

| 任务类别 | 题数 | Token 减少 | baseline 严格正确 | AIRP 严格正确 |
|---|---:|---:|---:|---:|
| exact_fact | 12 | 58.38% | 11/12 | 10/12 |
| behavior_reasoning | 4 | 58.16% | 4/4 | 3/4 |
| workflow_semantics | 4 | 59.10% | 3/4 | 3/4 |
| relationship_impact | 2 | 52.74% | 2/2 | 2/2 |
| hook_protocol | 1 | 51.06% | 1/1 | 1/1 |
| language_scope | 1 | 76.37% | 0/1 | 1/1 |

所有六类任务都观察到 51% 以上的总模型 Token 降幅；AIRP 没有执行源码工具调用。

## 失败归因与信息丢失

严格字符串评分混合了信息正确性、格式遵循和题目质量，因此逐项审计六个失败任务：

| 任务 | 组 | 归因 |
|---|---|---|
| behavior-003 | AIRP | **真实信息丢失**：只提供 checkout，缺少 discount 与 shipping 函数体 |
| workflow-002 | AIRP | 证据完整，模型把 11 项集合计为 12；属于模型推理错误 |
| exact-005 | AIRP | 三类路径语义均正确，但用中文回答，没有遵守英文标签格式 |
| exact-011 | 两组 | 题干“按 default\|smart 返回”有歧义，两组均照抄标签；该题排除 |
| workflow-001 | baseline | 三个状态值均正确，但附带标签；属于格式差异 |
| language-001 | baseline | 源码读取后仍把 12 种语言计为 11；属于模型推理错误 |

排除一项歧义题、接受语义等价格式后，baseline 信息正确率为 **22/23（95.65%）**，AIRP 为 **21/23（91.30%）**。可归因于 AIRP 上下文缺失的是 **1/23（4.35%）**，而不是严格评分中看到的四个失败。

## 根因修复与复测

针对审计结果完成三项修复：

1. 对短模块导入增加唯一后缀解析，支持嵌套脚本目录中的 `from pricing import ...`。
2. 计算/求值任务交付被调用函数的完整函数体，并把这些传递依赖纳入 sufficient 判定。
3. 对“集合有多少项”生成本地 AST 确定性 cardinality 事实，避免模型自行计数。

同时重写 exact-011 的歧义题干。三个受影响任务重新执行完整 baseline/premodel 配对：

| 指标 | baseline | 修复后 AIRP | 变化 |
|---|---:|---:|---:|
| 正确率 | 3/3 | 3/3 | 持平 |
| 总模型 Token | 101,832 | 42,557 | **−58.21%** |
| 源码命令调用 | 4 | 0 | −100% |
| 总耗时 | 71.39 s | 24.45 s | **−65.75%** |

复测原始结果：[premodel-hook-postfix-v18.json](../benchmarks/runs/premodel-hook-postfix-v18.json)。真实信息丢失题从错误的 `50.0` 恢复为正确的 `53.0`。这是针对性复测，不能冒充重新执行全部 48 次调用后的总体准确率。

## 修复后本地负载审计

使用与 Agent 实验完全相同的源码复制范围重新审计 24 题：Hook 触发与 sufficient 均为 24/24；MCP JSON 为 11,281 Token，Hook 为 9,024 Token，负载减少 **20.01%**。行为题增加传递依赖函数体后仍保持整体负载下降。

原始数据：[hook-payload-v17.json](../benchmarks/runs/hook-payload-v17.json)。

## 结论边界

结果证明预模型架构在这个约 2,000 行的真实 Python 项目、六类只读问题上显著减少总 Token 和工具调用。它尚未证明编辑任务、多语言真实仓库、十万行项目或长会话中的同比收益。下一轮应把同一方法扩展到不同代码规模和多个真实仓库，而不是继续增加同一仓库中的事实题。
