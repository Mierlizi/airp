# AIRP 对照实验起点

`run_hook_payload_audit.py` 对 24 个真实源码问题执行本地确定性预模型负载审计，不调用模型。`run_premodel_hook_ab.py` 执行普通源码读取与预模型注入的配对 Agent 实验，并按任务类别统计总模型 Token、正确率、命令调用和端到端延迟。完整 24 题结果保存在 `runs/premodel-hook-v17.json`；修复后针对性复测保存在 `runs/premodel-hook-postfix-v18.json`。

`run_multi_repo_edit_ab.py` 是当前最接近真实使用的扩展实验：4 个固定公开仓库、Python/JavaScript/Rust/C、268–5,054 行生产源码、8 个理解/关系任务和 4 个带可执行隐藏验收的编辑任务。24 次成对调用均已完成，两组均 12/12 成功，总模型 Token 减少 18.54%。任务、原始结果和分析分别见 `multi_repo_tasks_v19.json`、`runs/multi-repo-edit-v19.json` 和 `docs/V19_MULTI_REPO_EDIT_RESULTS_20260913.md`。样本尚不足以替代 50–100 个任务、多仓库重复运行的总体估计。
V34 使用同一 runner 扩展到 Django、NestJS、ripgrep、Redis 的真实大型仓库编辑。四组 baseline/AIRP 均通过隐藏验证，总模型 Token 减少 38.31%。任务定义为 `large_repo_edit_tasks_v34.json`；可发布原始结果为 `published/large-repo-edit-v34.json`；本地无模型检索测量由 `measure_edit_frontier.py` 生成 `published/large-edit-frontier-local-v34.json`。C 样本 Token 增加 1.57%，改进后的隔离 harness 提示还没有模型复测。完整方法与限制见 `docs/V34_LARGE_REPOSITORY_EDIT_FRONTIER_20260914.md`。

另有两版已运行的只读 Context Compiler 消融实验：3 个任务、每条件重复 2 次。v0.2 优化结果见 `docs/BENCHMARK_CONTEXT_AB_V02_20260912.md`，v0.1 结果见 `docs/BENCHMARK_CONTEXT_AB_20260912.md`。它们验证完整文件与 AIRP 符号上下文的差异，不能替代完整 Agent 插件 Benchmark。

每个任务从同一份干净代码开始。固定模型、版本、模型参数、提示词、测试约束和时间上限，分别运行 baseline（文件/搜索/shell）和 airp（加 AIRP 工具）。两组均使用相同验收测试，由独立验收决定 success。重复多轮并轮换运行顺序。原始模型 token 用量从实际宿主或 API 记录，AIRP metrics 的响应字节数不能替代它。

`tasks.json` 是任务种子，验收条件需要转换为隐藏测试后才能形成可信实验。它们不是已经跑过的模型任务。

`run_task_pack_ab.py` 是 v0.4 的本地协议基准，比较 `find + context` 与自然语言一步 context pack 的调用数、响应字节和 receipt 增量；它不调用模型，因此结果不能替代 Token A/B。`run_scale_index.py` 同时测量已知符号查询和自然语言任务检索的延迟。

`run_language_scale_tokens.py` 构造 Python、JavaScript、TypeScript、Go、Rust、Java、C#、C、C++、Ruby 和 PHP 的四档仓库，比较“搜索加完整文件”与一次 AIRP auto task context 的完整响应 Token。它要求正确锚点和答案标记后才报告降幅，并生成 JSON、CSV 和 SVG 热力图。该实验测量上下文交付协议，不包含模型调用。

`run_synthetic_model_stage_ab.py` 仅向真实模型发送生成代码，在隔离空目录中完成11种语言、2次重复的配对精确答案实验。它保存实际模型输入、缓存输入、推理输出和最终输出 Token。`render_comprehensive_token_chart.py` 将该结果与矩阵的横向/纵向平均合成一张 SVG。

`run_task_magnitude_model_ab.py` 用1、3、8、16个必需代码依赖定义四种任务量级，每档运行3次baseline/AIRP真实模型配对。`render_task_magnitude_chart.py` 生成总Token、绝对减少和代码载荷降幅图。

`render_task_magnitude_impact_matrix.py` 从同一份真实模型记录生成一张任务量级影响矩阵，横向合并代码上下文、模型总Token、墙钟耗时和准确率，未把未测试的编辑或修复任务填入图中。

`run_task_magnitude_expanded_ab.py` 将量级扩大到1、3、8、16、32、64个依赖，每档运行10种不同答案结构，共120次真实模型调用，并用成对自助法报告耗时置信区间。`render_task_magnitude_expanded_matrix.py` 生成替代v0.6先导图的v0.7综合矩阵。

`audit_required_information_loss.py` 将模型准确率与插件证据丢失分开，逐题核对目标表达式和依赖名称/默认值。`render_code_size_information_matrix.py` 直接按非空代码行数和代码Token展示筛减率、必要信息丢失率、模型Token与速度。

将实际运行记录保存为 JSONL，每行包含：

| 字段 | 内容 |
|---|---|
| task_id | 任务标识 |
| group | baseline 或 airp |
| model | 完整模型版本 |
| revision | 初始源码版本或快照哈希 |
| run | 重复实验序号，两组必须一致 |
| success | 布尔值，由统一验收判定 |
| input_tokens / output_tokens | 实际 Token 统计 |
| cost_usd | 同口径实测成本，包含失败任务费用 |
| tool_calls | 工具调用总数 |
| latency_seconds | 总耗时 |

运行 `python benchmarks/summarize.py 实测记录.jsonl`。汇总器拒绝缺少配对的记录，报告成功率、Token 降幅、平均调用数及每个成功任务的成本。零成功时成本指标为空，不产生除零或虚假结论。
