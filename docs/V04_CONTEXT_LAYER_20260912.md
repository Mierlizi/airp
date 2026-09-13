# AIRP v0.4 通用上下文层

## 实现

`airp_context` 现在有两种稳定入口：已知符号时传 `symbol_id`；只有任务描述时传 `task`。任务入口先以本地词法 IDF 排序候选符号，再从最多 1、2 或 3 个任务锚点扩展符号图，最后用相似度惩罚降低近重复源码的优先级。整个过程不调用 embedding 或生成模型。

`breadth=narrow|balanced|broad` 控制任务锚点上限。正文继续执行完整符号边界和字节或 tokenizer 硬预算。`receipt_id` 表示 Agent 当前仍持有的符号哈希，后续 pack 只补充未持有或已变化的符号。

没有为这一能力增加新的 MCP 工具。Codex、Cursor、Claude Code 或其他支持 stdio MCP 的宿主可以复用同一个 `airp_context` 接口；不支持 MCP 的宿主可以调用 JSON CLI 的 `airp pack`。

## 本地协议基准

`benchmarks/run_task_pack_ab.py` 在原有 3 个代码理解任务上比较：

- 旧路径：`find` 加 `context`，共 6 次调用。
- 新路径：自然语言 `task_context`，共 3 次调用。
- 新路径 3/3 正确定位预期符号。
- 序列化响应从 12,728 字节降到 11,016 字节，减少 13.45%。
- 同任务携带 receipt 再调用时，正文从首次合计 6,823 字节降到 631 字节，减少 90.75%。

这是本地确定性协议实验，不是模型 Token A/B。样本只有 3 个本仓库任务，证明接口和增量机制有效，不证明真实任务成功率或通用 Token 收益。

## 验证

- 自动化测试：34 passed。
- MCP 和已安装插件缓存冒烟测试：7 个工具，自然语言任务入口通过。
- 5,000 文件合成仓库：自然语言任务检索 0.454 秒，并正确定位 `func_4999`；已知符号上下文为 0.342 秒。
- 中英文检索：支持 snake_case、camelCase 和连续中文的双字切分；跨语言同义词仍需要可选 embedding 或词典后端。
