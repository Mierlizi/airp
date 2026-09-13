# AIRP v0.3 交付记录

日期：2026-09-12。依据：工作区 `AIRP_产品规划文档_v2.0.docx` 与引用对话中最终确定的 Tool-first / Local-first 路线。

## 交付结果

可运行的本地多语言程序操作层：逐文件增量解析 → SQLite 查询索引 → 符号与关系查询 → 排序上下文 → Python 函数替换 → 验证、测试与回滚。CLI 输出 JSON，插件通过 stdio 提供一个组合 MCP 入口。Tree-sitter 与真实 Tokenizer 为可选后端。

另已封装并安装个人 Codex 插件 `airp@personal`。插件包含自动发现的 `airp-program-operations` Skill、MCP 清单和产品元数据；源码位于 `plugins/airp`，个人 marketplace 源位于 `C:/Users/23747/plugins/airp`。

## 实际验证

| 检查 | 结果 |
|---|---|
| `python -m pytest -q` | 41 passed |
| `python scripts/demo.py` | 成功提交；故意错误触发测试失败；回滚后全部测试恢复 |
| `python scripts/check_mcp.py` | 真实客户端握手、单工具发现、自动索引、查询、更新、验证、测试、提交、错误传播通过 |
| `python scripts/check_plugin.py` | 从插件 `.mcp.json` 启动服务，发现 1 个工具并在一次 context 调用中索引临时仓库 |
| `python benchmarks/run_language_matrix.py` | JavaScript、TypeScript/TSX、Go、Rust、Java、C/C++、C#、Ruby、PHP 共 11/11 成功 |
| `python benchmarks/run_scale_index.py --files 5000` | 首次 15.68 秒；无变化 0.76 秒；单文件变化 0.78 秒；查询 0.30 秒 |

测试覆盖：跨文件调用与测试候选、稳定地址、增删文件导致索引过期、预算不截断函数、错误哈希、修改预览、语法错误拒绝、外部修改冲突、空测试集不能提交、旧测试结果失效、跨文件回滚、会话重开恢复、二次写入中断后的恢复、装饰器/异步方法/中文/CRLF、CLI JSON 错误与 Benchmark 记录配对。

MCP 联调发现并修复了 Windows 测试子进程继承服务输入管道而阻塞的问题，测试进程现在使用独立的空输入。

## 与完整规划的差距

当前已具备普适的结构索引入口，但没有把结构能力等同于编译器级语义。非 Python 后端尚无跨文件调用图和受控写入；Python 关系仍可能受动态分派影响。进一步精度需要接入 Pyright/Jedi、SCIP 或语言服务器。HTTP 服务、真正安全的缩减测试集以及 50–100 个真实编辑任务仍未完成。

默认预算仍为通用 UTF-8 字节上限；安装 tokens 可选依赖后可使用指定 tokenizer 的精确硬预算。编辑具备持久备份与单文件原子替换，但多文件事务不保证崩溃时所有文件同时切换。详细边界见根目录 README。

v0.2 小样本只读消融中，两组均为 6/6 正确，AIRP 输入 Token 减少 21.81%，输入与输出合计减少 21.83%；详见 `docs/BENCHMARK_CONTEXT_AB_V02_20260912.md`。该结果不包含完整编辑 Agent 的工具往返成本。

## 下一阶段建议

首先在真实多仓库任务中冻结验收并运行 baseline/AIRP 配对实验；再按错误数据接入语言服务器或 SCIP 精确关系，并用 Git 变更清单降低超大仓库的文件枚举成本。商业规划中的成本收益仍待验证。
