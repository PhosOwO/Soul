# Soul / DeepSeek Harness State A/B v0.2

这个实验用 DeepSeek Harness 跑三组对照，目标是观察 Soul 的价值是否来自可演化的结构化 Current State，而不是简单“多塞一段提示词”。

## 三组

- `baseline`：同一个 Harness、同一个模型、同一个当前任务，但没有前置记忆。
- `memory`：同一个 Harness 和任务，额外给一段普通自然语言摘要记忆。
- `soul`：同一个 Harness 和任务，不把状态拼进 task，而是通过 `@deepseek-ai/dsh-soul-context` 在 `agent/pre-step` 注入 Current State，并在 `turn/end` 收集 evidence 请求 State Patch Proposal。

## 运行

在 Soul 仓库根目录执行：

```powershell
python experiments\dsh_state_ab_v0_2\run_dsh_parallel_experiment.py
```

如果没有可用模型 key，使用：

```powershell
python experiments\dsh_state_ab_v0_2\run_dsh_parallel_experiment.py --dry-run
```

## 输出

输出写入：

```text
experiments/dsh_state_ab_v0_2/outputs/latest/
```

主要文件：

- `metadata.json`：运行参数、模型、三组变量控制说明。
- `results.json`：结构化结果。
- `results.md`：人读报告。
- `raw/*.jsonl`：每个 Harness 进程的原始输出。
- `project/.soul/state/state.json`：Soul 组确认后的 New State。
- `project/.soul/state/patch_proposals.jsonl`：Harness `turn/end` 产生的 patch proposal。
- `project/.soul/reme/`：`memoryMode: soul_reme` 时的 ReMe memory/evidence workspace。

## 公平性说明

这个实验不是“有记忆 vs 没记忆”的展示。`memory` 组被设计成一个更公平的中间基线：它拥有同样的历史事实摘要，但没有 Soul 的结构化 state、版本、patch proposal、confirm gate 和可追踪 history。最终要观察的是：当链路变长以后，Soul 是否比普通摘要更稳定地保持约束、避免重复建议，并把未验证方向保留为下一步行动线索。
