# AIRP Agent 使用指南

目标：先查询结构，再按需读源码，使用可验证的局部修改。

1. 简单单文件或精确值查询使用普通搜索；预计需要多个源码读取或结构关系时调用一次 `airp(operation="context", task=...)`。它会自动建立或刷新索引。
2. 检查 `anchors` 和 `sufficient`。证据不足时只做一次有明确假设的 `find`、`get`、`refs`、`dependencies`、`callers` 或 `affected` 操作。
3. Python 修改先 `prepare`，再用 `update` 和 `options.dry_run=true` 预览；`begin` 后以 `dry_run=false` 应用。
4. 用 `verify` 与 `test` 验证，成功后 `commit`，否则修复或 `rollback`。测试会执行仓库代码，只用于已授权的可信仓库。

不要将静态候选描述为完整调用图，不要据候选测试省略其他测试，不要把响应字节数当作模型 Token 消耗。过期索引需要重建，hash 冲突需要重新读取。事务外部冲突需要保留用户修改并报告，不尝试强制覆盖。
