# AIRP 扩展任务覆盖实验

## 覆盖范围

任务集从 12 个精确事实题扩展为 24 题、6 类任务：

| 类别 | 题数 | 内容 |
|---|---:|---|
| exact_fact | 12 | 默认值、常量、集合、条件和协议字段 |
| behavior_reasoning | 4 | 分支行为、跨函数数值传播、自适应预算 |
| workflow_semantics | 4 | 索引状态、只读边界、验证与编辑预览 |
| relationship_impact | 2 | 意图路由、调用者和受影响测试证据 |
| hook_protocol | 1 | UserPromptSubmit 输出协议 |
| language_scope | 1 | 多语言映射范围 |

任务定义：[real_source_tasks_v17.json](../benchmarks/real_source_tasks_v17.json)。全部任务使用当前真实项目源码，答案由源码中的常量、控制流或明确协议结构确定。

## 新增方法修复

1. Hook 不再统一使用 `understand`。调用链、caller 和影响问题使用 `impact`；修改问题使用 `edit`；测试问题使用 `test`；其他代码理解使用 `understand`。
2. 覆盖检查统一规范化 `airp.hook._budget_for`、`airp/hook.py::_budget_for` 等等价表示，避免模块点号路径和 AIRP 文件符号地址产生假缺失。
3. 实验结果新增按任务类别汇总，可分别观察 Token、正确率、工具调用和延迟，避免总体平均掩盖关系任务的高负载。
4. 小数答案使用数值序列验收，使 `53` 与 `53.0` 等价，同时保留顺序检查。

## 本地确定性审计

| 指标 | 旧 MCP JSON | 预模型 Hook | 变化 |
|---|---:|---:|---:|
| 24 题上下文 Token | 11,281 | 9,024 | **−20.01%** |
| Hook 触发 | — | 24/24 | 全覆盖 |
| sufficient | — | 24/24 | 100% |

计数使用本地缓存的 `o200k_base` tokenizer。原始结果：[hook-payload-v17.json](../benchmarks/runs/hook-payload-v17.json)。这只证明检索负载和覆盖，不替代真实模型总 Token 实验。

## 模型实验状态

配对脚本 [run_premodel_hook_ab.py](../benchmarks/run_premodel_hook_ab.py) 默认执行 24 题 × baseline/premodel 两组，共 48 次模型调用，并输出整体及分类统计到 `benchmarks/runs/premodel-hook-v17.json`。每个任务对完成后独立落盘；使用 `--resume` 可在额度、网络或进程中断后继续。用户已明确允许后续模型实验和所选源码片段发送。

扩展实验已完成：48/48 次调用均产生结果。修复前总模型 Token 减少 59.06%，严格正确率为 baseline 21/24、AIRP 20/24；逐项信息审计发现 AIRP 真实信息丢失 1/23，修复后的三题配对复测为两组均 3/3。完整结果、失败归因和限制见 [V15 扩展 Agent 实验](V15_EXPANDED_AGENT_RESULTS_20260913.md)。
