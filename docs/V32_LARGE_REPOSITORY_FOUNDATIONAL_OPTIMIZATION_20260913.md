# V32 大型仓库底层算法优化与实验

日期：2026-09-13

## 结论

AIRP 新增了精确符号快速启动、显式目标固定、Python 重载与属性访问器消歧、创建型编辑规划、异常边界分析、有损错误抑制分析、目标测试限流、语言感知验证提示和 `.airpignore`。这些修改针对大型仓库的两个主要成本：首次完整建图耗时，以及证据不足导致模型再次搜索。

四个固定提交的大型真实仓库阅读实验中，基线与 AIRP 均为 4/4 正确。总模型 Token 从 224,923 降至 76,852，减少 65.83%；工具调用从 9 降至 1，减少 88.89%；模型阶段累计耗时从 139.454 秒降至 90.219 秒，减少 35.30%。这是四题配对实验，不是总体市场收益估计。

## 大型仓库快速启动

旧算法在 Django 上构建全量图超过约六分钟仍未完成，因此中止。新算法在无持久图、源码文件不少于 500 且任务包含精确代码标识符时：

1. 扫描文件中的精确词边界；限定名要求所有者和成员同时出现在同一文件。
2. 只对候选文件运行 AST 或 Tree-sitter。
3. 优先生产代码、非 vendor 路径和有函数体的实现。
4. 返回一个精确实现，保持证据包在任务预算内。
5. 模糊查询、调用链和影响分析仍使用持久完整图。

| 仓库 | 固定提交 | 语言 | 索引源码行 | 生产源码行 | 首包耗时 | 上下文字符 | 锚点 |
|---|---|---:|---:|---:|---:|---:|---|
| Django | `2b30f625` | Python | 532,132 | 165,957 | 3.7149 s | 1,898 | `QuerySet.get` |
| NestJS | `a3a31b96` | TypeScript | 133,789 | 61,854 | 0.4972 s | 1,711 | `NestFactoryStatic.create` |
| ripgrep | `3fce3b5b` | Rust | 56,409 | 49,796 | 0.8767 s | 693 | `PatternMatcher` |
| Redis | `669b2a13` | C | 383,301 | 327,855 | 0.8098 s | 616 | `dictFind` |

ripgrep 只有 111 个源码文件，低于快速启动阈值，0.8767 秒包含完整持久图构建。其余三项使用精确符号快速启动。原始数据见 `benchmarks/runs/large-repo-fast-start-v31.json`。

## 真实模型配对结果

模型为 `gpt-5.6-luna`、推理强度 low。每个任务分别运行普通仓库工具基线和首次请求前注入 AIRP 证据的实验组。答案用任务特定语义规则评分；两组使用相同规则。原始答案、Token、命令轨迹和固定提交保存在 `benchmarks/runs/large-repo-read-v32.json`。

| 语言/仓库 | 基线 Token | AIRP Token | 减少 | 基线/AIRP 正确 | 工具调用 基线→AIRP |
|---|---:|---:|---:|---:|---:|
| Python / Django | 48,648 | 14,316 | 70.57% | 1/1 | 2→0 |
| TypeScript / NestJS | 61,687 | 14,337 | 76.76% | 1/1 | 3→0 |
| Rust / ripgrep | 45,689 | 14,057 | 69.23% | 1/1 | 2→0 |
| C / Redis | 68,899 | 34,142 | 50.45% | 1/1 | 2→1 |
| **合计** | **224,923** | **76,852** | **65.83%** | **4/4** | **9→1** |

## 编辑任务修复

V19 的多仓库实验已显示编辑任务平均只减少 6.22%，且 `partial` 证据全部反增。本轮针对根因加入：

- 精确编辑目标只固定一个实现，并最多提供两个相关测试。
- 新 API 请求使用创建上下文规划器，同时选择实现文件、声明文件和附近惯例。
- Python `@overload`、属性 setter/deleter、分支内重名局部函数不再导致整模块丢失。
- 对“把异常转换为领域错误”类任务，静态标出 `try` 之前的未保护语句。
- 识别 `errors="ignore"`、Rust `.ok()` 和空 catch 等吞错结构。
- 注入精确编辑行号和仓库布局对应的最小验证环境。

在 itsdangerous 的行为编辑复测中，两组均通过隐藏验证，AIRP 将模型 Token 从 294,925 降到 79,395，减少 73.08%；工具调用从 14 降到 4；耗时从 168.531 秒降到 57.250 秒。原始数据见 `benchmarks/runs/python-edit-suppression-v26.json`。V25 虽降至 46,009 Token，但保留了吞错参数并失败，因此未计为有效收益。

## 适用边界

大型仓库的 65.83% 是精确、局部、可由单个实现回答的阅读任务结果。跨模块影响分析、模糊概念搜索、动态分派和大型编辑仍需要完整图、语言服务器或运行时验证，收益不会同步等比例出现。本轮没有完成四种大型仓库的可执行编辑配对，因此不能宣称大型编辑普遍减少 65.83%。

快速启动以文件数 500 为阈值，仍需线性读取源码字节；Django 首包 3.7 秒说明后续应增加轻量持久候选目录或 VCS 变更清单。非 Python Tree-sitter 后端目前主要提供结构证据，跨文件调用关系仍弱于 Python。

## 复现

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -B benchmarks/run_large_repo_scale.py --output benchmarks/runs/large-repo-fast-start-v31.json
.\.venv\Scripts\python.exe -B benchmarks/run_multi_repo_edit_ab.py --tasks benchmarks/large_repo_tasks_v32.json --corpus benchmarks/corpus-large --output benchmarks/runs/large-repo-read-v32.json --workers 2 --resume
```
