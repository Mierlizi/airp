# AIRP v0.6

面向 Coding Agent 的本地程序操作层。v0.6 在 SQLite FTS5 召回和自适应取证之上增加成本感知响应：普通 MCP 调用自动去除审计元数据，并在单个小文件更便宜时直接交付完整文件。它保留预算、锚点、receipt 和 Python 受控编辑安全性。核心不调用模型 API。

## Codex 插件

项目已包含可安装插件 [plugins/airp](plugins/airp)。当前版本通过 `UserPromptSubmit` Hook 在第一次模型请求前完成本地索引和自适应检索，从根本上消除分析型 MCP 的“模型 → 工具 → 模型”额外往返。默认插件不加载 MCP；独立 CLI 和 MCP 适配器仍保留供显式兼容与事务流程使用。当前机器已安装 `airp@personal` 版本 `0.6.0+codex.20260913073156`，新建 Codex 任务后加载。

插件开发验证：

```powershell
.\.venv\Scripts\python.exe scripts/check_plugin.py
```

从 GitHub 检出后，可先预览再安装到个人 Codex Marketplace：

```shell
python scripts/install_local_plugin.py --dry-run
python scripts/install_local_plugin.py
```

## v0.2 Token 实验

已完成 3 个代码理解任务、每条件重复 2 次的新版只读配对实验。两组均为 6/6 正确；AIRP 输入 Token 减少 **21.81%**，输入加输出合计减少 **21.83%**。相较 v0.1 的输入降幅提高 5.52 个百分点。结果验证 Context Compiler，不代表完整 Agent 工作流。方法、原始记录与限制见 [v0.2 实验报告](docs/BENCHMARK_CONTEXT_AB_V02_20260912.md)。

## v0.6 跨语言协议实验

11 种语言、4 档仓库规模共 44 个合成交点全部正确。相对“搜索命中+读取完整文件”，`auto` 上下文响应 Token 平均减少 **81.28%**，范围 77.87%–83.80%。这是不调用模型的上下文交付协议实验，不是完整 Agent 总 Token 降幅。见 [v0.6 成本感知报告](docs/V06_COST_AWARE_CONTEXT_20260912.md)。

## 自适应检索与成本路由

最新实现按任务自动选择 narrow、balanced 或 broad，并把任务中点名的代码标识符纳入证据充分性检查。12 个真实源码事实问题的完整响应从 7,066 降到 5,993 个 `o200k_base` Token，减少 **15.19%**，证据充分率保持 12/12；成本路由把这 12 个简单问题全部判定为应使用普通搜索。详见 [基础检索方法优化](docs/V12_FOUNDATIONAL_RETRIEVAL_OPTIMIZATION_20260913.md)。

## 预模型 Hook 架构

新的方向不再排除简单代码任务。12/12 个真实源码问题全部由 AIRP 服务且证据状态均为 sufficient。相对旧 MCP JSON，Hook 注入负载从 6,024 降到 4,614 个 `o200k_base` Token，减少 **23.41%**。2 题真实 Agent 预实验中，两组均 2/2 正确，总模型 Token 从 72,272 降到 43,300，减少 **40.09%**；完整 12 题实验受账户用量上限阻塞，因此该比例只是预实验结果。方法、原始数据和限制见 [预模型架构报告](docs/V13_PREMODEL_HOOK_ARCHITECTURE_20260913.md)。

任务覆盖现已扩大到 24 题、6 类，包括行为推理、工作流语义、关系/影响、Hook 协议和语言范围。48 次真实模型配对调用全部完成：总模型 Token 从 835,836 降到 342,218，减少 **59.06%**；工具调用从 31 次降到 0；耗时减少 **56.21%**。严格正确率 baseline 为 21/24、AIRP 为 20/24；信息审计确认 AIRP 上下文真实丢失为 1/23，完成传递依赖修复后的针对性复测为两组均 3/3，Token 减少 **58.21%**。详见 [完整实验与失败归因](docs/V15_EXPANDED_AGENT_RESULTS_20260913.md)。

最新 V19 实验进一步覆盖 4 个公开真实仓库、Python/JavaScript/Rust/C、268–5,054 行生产源码以及 4 个隐藏验证编辑任务。两组均 12/12 成功，总模型 Token 减少 **18.54%**，工具调用减少 **22.00%**；理解/关系任务减少 **46.66%**，编辑任务减少 **6.22%**。9 个 `sufficient` 任务全部节省，3 个 `partial` 任务全部反增，说明充分性状态应进入下一版成本路由。详见 [多仓库与编辑任务报告](docs/V19_MULTI_REPO_EDIT_RESULTS_20260913.md)。

V32 增加大型未索引仓库的精确符号快速启动。在 Django、NestJS、ripgrep、Redis 四个固定提交（56,409–532,132 行索引源码）的配对模型实验中，两组均 4/4 正确，总模型 Token 从 224,923 降到 76,852，减少 **65.83%**；工具调用从 9 降到 1。首次证据包耗时 0.50–3.71 秒、大小 616–1,898 字符。另一个 Python 行为编辑复测两组均成功，Token 减少 **73.08%**。详见 [大型仓库底层优化报告](docs/V32_LARGE_REPOSITORY_FOUNDATIONAL_OPTIMIZATION_20260913.md)。

## 立即体验（Windows PowerShell）

当前项目已准备 `.venv`。在本目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/demo.py
```

演示在临时副本中完成：索引 → 查询 → 上下文 → 修改 → 测试 → 提交，再故意引入错误 → 测试失败 → 回滚 → 测试恢复。不会修改 `examples/shop` 原件。

手动查询：

```powershell
.\.venv\Scripts\python.exe -m airp --repo examples/shop index
.\.venv\Scripts\python.exe -m airp --repo examples/shop find discount
.\.venv\Scripts\python.exe -m airp --repo examples/shop get 'pricing.py::discount'
.\.venv\Scripts\python.exe -m airp --repo examples/shop callers 'pricing.py::discount'
.\.venv\Scripts\python.exe -m airp --repo examples/shop context 'pricing.py::discount' --budget 1500
.\.venv\Scripts\python.exe -m airp --repo examples/shop pack 'change discount and shipping behavior' --budget 1500
.\.venv\Scripts\python.exe -m airp --repo examples/shop test
```

其他机器安装（Python 3.11+）：

```shell
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e ".[mcp,test,universal,tokens]"
airp --repo . warm javascript typescript tsx go rust java c cpp csharp ruby php
airp --repo /absolute/path/to/repository index
```

核心也可以直接在项目根目录用 `python -m airp` 运行，无需 pip。`mcp` 和 `pytest` 都是可选依赖；默认测试执行器是 unittest。

## 已实现

| 能力 | v0.6 行为 |
|---|---|
| Program Database | Python AST 加可选 Tree-sitter；逐文件复用；SQLite 函数、类、模块赋值、入边、出边索引 |
| 稳定定位 | `相对文件路径::限定名称`；插入空行不改变地址，重命名/移动会改变地址 |
| 查询 | find / get / refs / callers / callees / dependencies / affected |
| Context Compiler | 大仓库精确符号快速启动；SQLite FTS5 候选召回、本地重排、符号图和多样性惩罚；大符号按任务提取聚焦源码行；自动紧凑响应与小文件成本旁路 |
| 受控修改 | 完整函数替换、默认 diff 预览、哈希前置条件、写前编译 |
| 事务 | begin / diff / commit / rollback；落盘前保留原始字节备份 |
| 验证与测试 | parse + compile、unittest / pytest、超时、有限输出、测试结果绑定 Python 源码快照 |
| AI 接口 | 默认插件使用 UserPromptSubmit 预模型 Hook；JSON CLI 和 stdio MCP 作为独立兼容接口保留 |
| 观测 | 工具次数、耗时、响应字节及成功调用数；5 个 Benchmark 种子任务与配对结果汇总器 |

## 修改流程

1. `index`，用 `find` 找到符号，用 `get` 取得当前 `hash` 和源码。
2. 将新函数（含应保留的装饰器）写入 UTF-8 文件，比如 `replacement.txt`。
3. 先预览，然后进入事务并应用：

```shell
airp --repo TARGET update 'pricing.py::discount' --source-file replacement.txt --expected-hash HASH
airp --repo TARGET begin
airp --repo TARGET update 'pricing.py::discount' --source-file replacement.txt --expected-hash HASH --apply
airp --repo TARGET diff
airp --repo TARGET verify
airp --repo TARGET test
airp --repo TARGET commit
```

失败时执行 `airp --repo TARGET rollback`。`commit` 是 AIRP 修改会话提交，不创建 Git commit。测试失败后会话保留，允许继续修复或回滚；没有自动丢弃修改。再次编辑必须获取新 hash，并重新测试。

默认执行 `python -m unittest discover -s tests -v`；其他布局可选 `test --runner pytest`。`--symbol-id` 会返回受影响测试候选，但实际仍跑全套，以免静态分析漏掉回归。测试会执行仓库代码，适用于可信本地仓库。

## MCP 接入

服务命令：

```powershell
.\.venv\Scripts\python.exe -m airp --repo 'D:\your\repository' serve
```

这是 stdio 服务，由 MCP 客户端启动后通过管道通信。它不会输出网页或启动 GUI。下面是支持 `mcpServers` 格式的客户端配置示例；请将两个绝对路径改成实际位置：

```json
{
  "mcpServers": {
    "airp": {
      "command": "D:/path/to/airp/.venv/Scripts/python.exe",
      "args": ["-m", "airp", "--repo", "D:/your/repository", "serve"]
    }
  }
}
```

建议给 Agent 的使用指引见 [docs/agent-guide.md](docs/agent-guide.md)。MCP 适配器采用 [官方 Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/)，多语言结构后端采用可选 `tree-sitter-language-pack`。本项目没有修改任何客户端的全局配置。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/demo.py
.\.venv\Scripts\python.exe scripts/check_mcp.py
```

`tests/` 检查编辑前置条件、冲突保护、字节恢复、过期索引、跨文件查询、CLI 错误及 Benchmark 配对约束。`check_mcp.py` 使用真实子进程和官方 MCP 客户端执行握手、工具发现、查询、编辑、测试和提交。

## 当前边界

- **这是工具 MVP，不是已商业验证的完整产品。** 已测得小样本只读 Context 消融中的 21.81% 输入 Token 降幅，尚未执行真实编辑 Agent A/B；不能据此宣称 30% 成本收益。实验方法见 [benchmarks/README.md](benchmarks/README.md)。
- 当前机器已预热 JavaScript、TypeScript、TSX、Go、Rust、Java、C、C++、C#、Ruby 和 PHP。其他 Tree-sitter 语言需要先运行 `warm`。非 Python 后端目前提供结构查询和上下文，受控写入仍限 Python 函数与方法。
- 调用和引用是 `static_candidate`，不是确定的运行时事实。局部遮蔽、继承、猴子补丁、动态导入、嵌套作用域、`src/` 导入根和外部模块可能漏报或误报；未解析边保留为 `unresolved`。受影响测试只是建议。
- Context 默认使用正文 UTF-8 字节上限；指定 `budget_unit=tokens` 和匹配的 tokenizer 后使用真实 Token 硬上限。JSON/MCP 固定元数据仍不计入正文预算。不会截断符号，放不下会列入 `omitted`。
- 索引逐文件复用，关系查询使用 SQLite 入边/出边索引；每次工具调用仍会遍历文件元数据检查新增、删除和时间戳，因此极大 monorepo 后续需要文件监视器或 VCS 变更清单。
- FTS5 只是词法候选召回，不是语义向量检索；同义改写、隐式概念和跨语言语义可能需要更具体的查询或外部语义后端。当 Python 的 SQLite 编译不含 FTS5 时，AIRP 会回退到全量本地词法打分。
- Python 调用和引用仍是保守静态候选。非 Python Tree-sitter 后端当前不构建跨文件调用图；类型、反射、动态分派和运行时绑定需要语言服务器、SCIP 或动态追踪后端。
- 同仓库 AIRP 操作由 OS 文件锁串行执行。单文件替换为原子写入，多文件事务不是跨文件崩溃原子提交；保留落盘备份供重启后 rollback。外部编辑不受 AIRP 锁控制，发现冲突时拒绝覆盖。不要同时让其他工具修改事务涉及的文件。
- 测试仅绑定 Python 源文件哈希，不覆盖数据文件、环境、依赖或配置变化。parse/compile 通过也不代表类型或行为正确。测试超时终止直接测试进程，测试自行启动的子进程不提供完整进程树隔离。
- 扫描排除常见依赖/构建目录和符号链接，并支持按仓库 `.airpignore` 排除路径，但不解释完整 `.gitignore` 语法。真正歧义的同级重复限定名仍会放弃该文件；Python overload、属性访问器和分支局部重名已有专门消歧。

## 目录

```text
airp/          索引、查询、编辑、事务、CLI、MCP
examples/shop/ 可运行的跨文件示例
scripts/       完整演示与 MCP 验证
tests/         自动化回归测试
benchmarks/    任务种子、配对实验汇总
docs/          AI 使用指南、初版交付说明
```
