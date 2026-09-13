# AIRP 多真实仓库、多语言与编辑任务实验（V19）

日期：2026-09-13

## 结论

在 4 个公开真实仓库、4 种语言、12 个任务的成对 Agent 实验中，baseline 与 AIRP 均为 **12/12 成功**。总模型 Token 从 **1,217,679** 降至 **991,910**，减少 **18.54%**；模型工具调用从 **50** 降至 **39**，减少 **22.00%**；端到端累计耗时从 **676.263 秒**降至 **648.251 秒**，减少 **4.14%**。

收益并不均匀。8 个理解和关系任务减少 **46.66%** Token，4 个编辑任务仅减少 **6.22%**。Rust 和 JavaScript 仓库分别减少 **54.14%** 和 **41.97%**；Python 基本持平（**1.01%**）；C 仓库反而增加 **26.39%**。这组结果支持“AIRP 已在部分真实任务产生可观效益”，但不支持“对所有仓库和编辑任务都会节省 Token”。

## 固定语料

代码量为本次检出的生产源码物理行数，不含测试、依赖、构建产物和空白筛除。

| 仓库 | 语言 | 固定提交 | 生产源码行 | 规模标签 | 任务 |
|---|---:|---|---:|---|---:|
| pallets/itsdangerous | Python | `672971d66a2ef9f85151e53283113f33d642dabd` | 1,176 | small | 3 |
| sindresorhus/p-limit | JavaScript/类型声明 | `783068bb9e967fd7bea8642e1bf5a3627fe38bdf` | 268 | tiny | 3 |
| sharkdp/fd | Rust | `b422e5d8c9cffaa1ae43ba68e7b97a60fb3e8ae5` | 5,054 | medium | 3 |
| antirez/sds | C | `5347739b1581fcba74fd5cab1fc21d2aef317d71` | 1,644 | small | 3 |

每个仓库包括 2 个源码理解或关系任务和 1 个编辑任务。编辑任务分别为 Python 非 ASCII Base64 错误封装、JavaScript 自定义清队列拒绝原因、Rust 无效正则的大写回退，以及 C 的二进制安全前缀 API。

## 方法

- 模型：`gpt-5.6-luna`，reasoning effort `low`。
- 每个任务分别运行 baseline 和 AIRP 一次，共 24 次模型调用；组间顺序按任务交替。
- 每次运行从固定提交复制新的隔离工作区。baseline 使用普通仓库工具；AIRP 在第一次模型请求中加入真实 `build_prompt_context` 输出。
- 理解任务用预先定义的语义事实量表评分。编辑任务忽略模型自述，由独立隐藏程序调用目标 API，并执行 Python、Node.js、Cargo 或 GCC 验证。
- AIRP 的本地索引时间计入端到端耗时，不产生模型 Token。依赖下载在正式计时前完成。
- 结果文件按任务落盘，支持断点恢复；一个最初未触发代码门控的 C 提示经明确加入“C function”后重新成对运行，原无效配对保存在 `benchmarks/runs/invalid-prompt-v19`，未纳入结果。

实验入口为 `benchmarks/run_multi_repo_edit_ab.py`，任务定义为 `benchmarks/multi_repo_tasks_v19.json`，逐调用原始事件汇总为 `benchmarks/runs/multi-repo-edit-v19.json`。

## 分组结果

| 分组 | baseline Token | AIRP Token | Token 变化 | baseline / AIRP 成功 | 工具调用变化 | 耗时变化 |
|---|---:|---:|---:|---:|---:|---:|
| 全部 | 1,217,679 | 991,910 | **-18.54%** | 12/12 / 12/12 | 50 → 39 | 676.263s → 648.251s |
| 理解/关系 | 366,200 | 195,330 | **-46.66%** | 8/8 / 8/8 | 15 → 5 | 202.186s → 120.313s |
| 编辑 | 846,576 | 793,950 | **-6.22%** | 4/4 / 4/4 | 35 → 34 | 474.077s → 527.938s |

编辑任务虽然减少 6.22% Token，却增加约 **11.36%** 耗时。主要原因是 C 编辑在 AIRP 条件下发生更多读取和编译尝试；预注入没有约束后续验证成本。

| 仓库 / 语言 | Token 变化 | 成功 | 工具调用 | 耗时 |
|---|---:|---:|---:|---:|
| fd / Rust | **-54.14%** | 3/3 → 3/3 | 13 → 4 | 184.639s → 88.235s |
| p-limit / JavaScript | **-41.97%** | 3/3 → 3/3 | 14 → 9 | 186.297s → 146.953s |
| itsdangerous / Python | **-1.01%** | 3/3 → 3/3 | 11 → 10 | 143.453s → 147.406s |
| sds / C | **+26.39%** | 3/3 → 3/3 | 12 → 16 | 161.874s → 265.657s |

规模标签与仓库、语言完全共线，不能解释为纯代码规模效应：medium（仅 fd）减少 54.14%，tiny（仅 p-limit）减少 41.97%，small（itsdangerous 与 sds）增加 14.65%。要估计规模效应，后续必须在同一语言和任务族内增加多个规模层级。

## 每任务结果

| 任务 | 类型 | baseline | AIRP | Token 变化 | 上下文状态 | 成功 |
|---|---|---:|---:|---:|---|---|
| itsdangerous `Signer.unsign` | 理解 | 46,863 | 14,590 | -68.87% | sufficient | 两组通过 |
| itsdangerous unsafe outcomes | 理解 | 44,208 | 45,954 | +3.95% | partial | 两组通过 |
| itsdangerous strict non-ASCII Base64 | 编辑 | 153,455 | 181,501 | +18.28% | partial | 两组通过 |
| p-limit `clearQueue` | 理解 | 49,684 | 14,164 | -71.49% | sufficient | 两组通过 |
| p-limit concurrency setter | 理解 | 47,940 | 29,405 | -38.66% | sufficient | 两组通过 |
| p-limit custom clear reason | 编辑 | 237,502 | 150,920 | -36.46% | sufficient | 两组通过 |
| fd uppercase HIR | 理解 | 45,571 | 29,601 | -35.04% | sufficient | 两组通过 |
| fd smart-case callsite | 关系 | 30,647 | 14,318 | -53.28% | sufficient | 两组通过 |
| fd invalid-regex fallback | 编辑 | 235,528 | 99,060 | -57.94% | sufficient | 两组通过 |
| sds growth policy | 理解 | 57,163 | 35,592 | -37.74% | sufficient | 两组通过 |
| sds range normalization | 理解 | 49,027 | 14,336 | -70.76% | sufficient | 两组通过 |
| sds binary prefix API | 编辑 | 220,091 | 362,469 | **+64.69%** | partial | 两组通过 |

`sufficient` 的 9 个任务中 9 个均节省 Token。`partial` 的 3 个任务全部反增。这是本轮最强的机制信号：当前 `status` 不只反映信息完整性，也可以作为是否值得注入或是否应缩短注入的成本路由依据。由于每格只有一个任务，仍需重复实验确认。

## 实现中发现并修复的问题

扩展语料暴露了两个真实跨语言检索问题：

1. JavaScript 的 `test.js` 匿名用例曾压过 `index.js` 中的生产实现。索引现在按常见测试目录和文件名标记所有语言的测试符号，并在非测试问题中降权。
2. Tree-sitter 将 `{clearQueue: {value() {...}}}` 的方法命名为 `value`，导致无法按 `clearQueue` 定位。索引现在恢复外层对象属性名；同时，大符号聚焦摘要会优先保留用户明确点名的 camelCase 标识符。

修复后，`clearQueue` 直接命中 `index.js::pLimit.clearQueue`，项目回归测试为 **55 passed**。

## 适用边界与下一步

本轮把证据从单仓库只读任务扩展到了真实多仓库、多语言和可执行编辑，但样本仍小：每个任务只运行一次，每种语言只有一个仓库，代码量最高约 5 千生产源码行。模型服务缓存、随机性和 Windows 工具链会造成方差，因此 18.54% 应视为本轮样本结果，而不是市场总体节省率。

下一轮最有价值的基础优化是让 `partial` 上下文进入保守模式：只注入最强锚点或直接不注入，并为编辑任务加入“修改点 + 必要声明 + 目标验证”的专用证据包。V19 的 3 个 partial 任务全部反增，而 9 个 sufficient 任务全部节省；这提供了明确、可检验的路由假设。
