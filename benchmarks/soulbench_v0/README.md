# SoulBench v0

SoulBench v0 是一个 AMBench 风格的最小基准，用来展示同一任务在三种上下文条件下的差异：

1. `baseline`：只有当前任务和项目背景，没有前置记忆。
2. `memory_summary`：加入普通自然语言摘要记忆。
3. `soul`：通过 Soul Core 的 `observe evidence -> propose patch -> review/apply -> projected state` 闭环生成 Current State，再把投影注入给 Agent。

默认版本使用本地 deterministic adapter，不调用外部模型，因此结果可复现、零成本，也不会伪造真实模型输出。`deepseek` backend 会真实调用 DeepSeek API，并在 `run_metadata.json` 里记录模型、token usage 和延迟。

## 运行

在仓库根目录执行：

```powershell
python benchmarks\soulbench_v0\run_soulbench.py
```

使用真实 DeepSeek API：

```powershell
$env:DEEPSEEK_API_KEY="..."
python benchmarks\soulbench_v0\run_soulbench.py --backend deepseek --output-dir benchmarks\soulbench_v0\results\deepseek_latest
```

先跑小样本 smoke test：

```powershell
python benchmarks\soulbench_v0\run_soulbench.py --backend deepseek --limit 3 --output-dir benchmarks\soulbench_v0\results\deepseek_smoke
```

不重新调用模型，只对已有 `results.json` 重新评分：

```powershell
python benchmarks\soulbench_v0\run_soulbench.py --rescore-from benchmarks\soulbench_v0\results\deepseek_latest\results.json --output-dir benchmarks\soulbench_v0\results\deepseek_latest_rescored
```

输出目录默认为：

```text
benchmarks/soulbench_v0/results/latest/
```

主要文件：

- `run_metadata.json`：运行时间、后端、任务数量、整体指标。
- `results.json`：逐 case、逐 adapter 的输出、评分、patch/state 信息。
- `benchmark_report.md`：面向人看的简洁报告。

## 评测口径

每个 case 都包含固定 evidence、当前 task 和显式 expected rubric。Soul 组不会拿到额外答案提示，只会先把 evidence 转成 Soul State Patch，自动应用 `auto_accept` 的 patch，然后把 task-relevant Current State 投影给同一个 answer backend。

这个基准刻意覆盖四类边界：

- 项目工程记忆：避免重复已否决方向，保持迁移约束。
- 研究连续性：Focal Loss 收益有限、暂不换 Backbone、MLD 未验证方向。
- 产品决策边界：区分已接受决策、待评审提案、明确约束。
- 过期或冲突记忆：避免旧猜测、未确认告警、临时 workaround 污染长期状态。

## 下一步接入点

SoulBench 当前已经把“状态机制”和“评测展示”解耦。当前已经支持：

- `--backend deepseek`：真实 DeepSeek API A/B/C，当前已支持。
- DeepSeek Harness plugin：通过 `@deepseek-ai/dsh-soul-context` 注入 Soul Current State。
- MCP server：通过 `soul-mcp` 暴露 `observe_evidence`、`propose_patch`、`apply_patch`、`get_projected_state`。
