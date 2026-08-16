# Soul Agent 行为实验设计 v0.1

## 1. 实验目的

本实验用于验证 Soul 的核心目标：

> State Transition 是否会改变 Agent 的实际行为。

Soul 当前已经完成从 Evidence Episode 到 State Patch，再到 New State 的工程闭环。下一步要验证的不是系统能否保存状态，而是：

- Agent 读取 Current State 后，是否会减少重复建议。
- Agent 是否能遵守已经接受的项目约束。
- Agent 是否能基于未解决问题提出更符合当前阶段的下一步行动。
- State Patch 产生的新状态，是否能在后续任务中改变 Agent 的回答策略。

因此，本实验关注 Agent 行为，而不是单纯关注存储、导入、反射或 CLI 功能。

## 2. 核心假设

### H1：连续性

拥有 Soul Current State 的 Agent 应该表现出更强的长期连续性。

当项目历史中已经记录某个方案被尝试、否定、暂停或降级时，Soul Agent 在后续任务中不应重新把该方案作为主要建议。

示例：

```text
历史状态：
Focal Loss 已尝试，收益有限，暂不继续作为主要方向。

新任务：
Recall 还是不足，下一步怎么办？

期望行为：
Agent 不应再次把 Focal Loss 作为首要建议，而应转向尚未验证的关键变量或采样策略。
```

### H2：一致性

拥有 Soul Current State 的 Agent 应该更稳定地遵守项目约束。

当 Current State 中存在已接受约束时，Soul Agent 应该在这些约束内提出方案，而不是给出通用但不适用的建议。

示例：

```text
历史状态：
当前阶段暂不更换模型 Backbone。

新任务：
如何继续提升模型效果？

期望行为：
Agent 不应优先建议更换 Transformer、ViT 或其他主干结构，而应在数据、特征、Loss、采样、阈值等范围内寻找方案。
```

### H3：主动性

拥有 Soul Current State 的 Agent 应该能主动发现当前状态中的缺口。

当 Current State 中记录了未解决问题、待验证假设或关键不确定性时，Soul Agent 应该能把这些内容转化为下一步行动，而不是只给出泛化建议。

示例：

```text
历史状态：
MLD / 海流等动力变量是否能提升 Recall 尚未验证。

新任务：
下一轮实验怎么设计？

期望行为：
Agent 应主动指出当前主要未知因素是动力变量贡献，并设计验证实验。
```

## 3. 实验对照组

### Baseline Agent

Baseline Agent 不读取 Soul Current State，只接收当前任务输入。

它代表普通无长期项目状态的 Agent，用于观察在缺少项目状态时是否会出现重复建议、约束遗忘和泛化回答。

### Memory Agent（可选）

Memory Agent 读取历史摘要或聊天记忆，但不区分 Evidence、Patch Proposal 和 Accepted State。

它用于对比“普通记忆”与“Soul 状态转移”的差异。该组不是第一轮 MVP 必需项，可以在 Baseline 与 Soul Agent 差异初步成立后加入。

### Soul Agent

Soul Agent 在回答前读取 Soul Current State。

它只把 Current State 作为已接受项目认知，不把原始 Episode 或未应用的 Patch Proposal 当作确定事实。

Soul Agent 的理想行为是：

- 避免重复已否定或已降级的方案。
- 遵守当前项目约束。
- 接住开放问题和待验证假设。
- 给出符合当前项目阶段的下一步行动。

## 4. 控制变量

为了避免实验结论退化为“信息更多所以当然更好”，实验必须控制变量。

固定项：

- 相同模型。
- 相同系统提示词骨架。
- 相同任务输入。
- 相同输出格式要求。
- 相同评估标准。
- 相同测试案例顺序。

变化项：

- Baseline Agent：不提供历史状态。
- Memory Agent：提供普通历史摘要。
- Soul Agent：提供 Soul Current State。

第一轮实验只改变状态输入，不同时调整模型、提示词策略、工具能力或任务描述。

## 5. 实验架构

建议实验结构：

```text
experiments/
  agent_behavior/
    cases/
      research/
      software_engineering/
      open_discussion/
    prompts/
      baseline_agent.md
      memory_agent.md
      soul_agent.md
    runner.py
    evaluator.py
    results/
```

实验流程：

```text
Test Case
   |
   +--> Baseline Agent
   |
   +--> Memory Agent（可选）
   |
   +--> Soul Agent
            |
            v
       Agent Answer
            |
            v
        Evaluator
            |
            v
        Metrics Report
```

每个 Case 应包含：

- `current_task`：当前用户请求。
- `accepted_state`：Soul Agent 可读取的 Current State。
- `memory_summary`：Memory Agent 可读取的普通历史摘要，可选。
- `expected_behavior`：期望行为。
- `failure_patterns`：典型失败模式。
- `evaluation_rubric`：评价维度。

## 6. 测试案例设计

第一批案例应覆盖三类长期协作场景：科研、软件工程、开放讨论。

### 6.1 科研项目案例

科研场景适合测试长期连续性、实验路线选择和未解决假设跟踪。

案例方向：

1. 已尝试方案不应被重复推荐。
2. 已暂停方向不应被重新推为主线。
3. 未验证变量应被主动转化为实验。
4. 评价指标变化应影响下一步策略。
5. 数据限制应约束实验建议。

示例 Case：

```text
Accepted State:
- 当前核心问题是 MHW 预测中的 Recall 不足。
- Focal Loss 已尝试，收益有限，暂不作为主线。
- Threshold tuning 已做过，提升有限。
- MLD / 海流动力变量尚未验证。

Current Task:
Recall 还是不足，下一步实验怎么设计？

Expected Behavior:
- 不把 Focal Loss 或阈值调节作为首要建议。
- 主动提出验证 MLD / 海流变量的实验。
- 给出可执行的对照实验设计。
```

### 6.2 软件工程案例

软件工程场景适合测试架构约束、已废弃路径和当前实现方向的一致性。

案例方向：

1. 已废弃兼容路径不应被重新引入。
2. 当前架构原则应影响实现建议。
3. 已完成迁移应被视为事实。
4. 未完成模块应成为下一步工作重点。
5. 测试策略应围绕当前风险展开。

示例 Case：

```text
Accepted State:
- Soul 的核心循环是 Current State -> Evidence -> Cognitive Diff -> State Patch -> Confirm -> New State。
- Soul 不再保留 Entity / Candidate 兼容路径。
- CLI 当前主命令是 init、status、context、state、agent、codex、import、episode、reflect。

Current Task:
下一步应该怎么完善 Soul 的反射能力？

Expected Behavior:
- 不建议恢复 Entity / Candidate 兼容命令。
- 围绕 State Patch Discovery、NO_CHANGE 判断和 Agent 行为验证提出计划。
- 把实验重点放在状态是否改变 Agent 行为，而不只是存储结构。
```

### 6.3 开放讨论案例

开放讨论场景适合测试 Agent 是否能延续用户的偏好、表达方式和共同概念。

案例方向：

1. 延续已形成的术语。
2. 遵守用户偏好的表达语言。
3. 不重复已经否定的定义。
4. 主动指出当前讨论中的认知缺口。
5. 在抽象讨论中保持项目目标一致。

示例 Case：

```text
Accepted State:
- 用户平时更希望 Soul 文档使用中文。
- 当前讨论重点是 Agent 行为实验，而不是继续扩展记忆概念。
- Soul 的目标是项目认知状态演化，不是普通聊天记忆。

Current Task:
帮我写下一版实验说明。

Expected Behavior:
- 使用中文。
- 聚焦 Agent 行为实验。
- 不把 Soul 重新描述成普通 memory system。
- 主动补充实验对照组、控制变量和评估指标。
```

## 7. 评价指标

### 7.1 重复建议次数

衡量 Agent 是否重复提出已经尝试失败、已暂停或已否定的方案。

评分建议：

- 0：没有重复建议。
- 1：轻微提及，但不是主要建议。
- 2：把历史否定方案作为重要建议。
- 3：把历史否定方案作为首要建议。

### 7.2 连续性

衡量 Agent 是否正确接住项目当前状态，而不是重新开始。

可观察信号：

- 是否引用当前问题。
- 是否承认已尝试路径。
- 是否沿着已有实验路线推进。
- 是否避免泛化回答。

### 7.3 约束一致性

衡量 Agent 是否遵守 Current State 中的已接受约束。

可观察信号：

- 是否违反明确约束。
- 是否提出与当前阶段冲突的建议。
- 是否能在约束内寻找替代路径。

### 7.4 行动有效性

衡量 Agent 的建议是否能转化为下一步行动。

可观察信号：

- 是否给出具体实验、实现或讨论步骤。
- 是否有优先级。
- 是否说明预期观察结果。
- 是否能区分主线任务和可选探索。

### 7.5 主动发现能力

衡量 Agent 是否能从 Current State 中发现未解决问题。

可观察信号：

- 是否识别待验证假设。
- 是否提出关键未知因素。
- 是否把开放问题转化为行动计划。
- 是否发现当前状态缺少哪些证据。

## 8. 第一轮 MVP 实验范围

第一轮 MVP 不追求大规模自动评测，目标是验证实验链路成立。

建议范围：

- 只比较 Baseline Agent 与 Soul Agent。
- 暂不加入 Memory Agent，避免第一轮变量过多。
- 使用 9 个测试案例：
  - 科研项目 3 个。
  - 软件工程 3 个。
  - 开放讨论 3 个。
- 每个案例固定相同模型、相同任务、相同输出要求。
- 每个回答先进行人工评估，再沉淀为半自动 evaluator 规则。
- 产出一份 `results_v0.1.md`，记录每组回答、评分和观察。

MVP 成功标准：

- Soul Agent 的重复建议次数低于 Baseline Agent。
- Soul Agent 的约束违反次数低于 Baseline Agent。
- Soul Agent 在多数案例中能更明确地承接 Current State。
- 至少出现若干 Baseline 泛化回答、Soul Agent 状态驱动回答的对比例子。

MVP 不要求：

- 完全自动评分。
- 证明 Soul 在所有任务上更好。
- 引入复杂记忆检索。
- 引入多模型对比。
- 把评估指标一次性产品化。

## 9. 后续扩展方向

### 9.1 引入 Memory Agent 对照组

在 Baseline 与 Soul Agent 的差异初步成立后，引入 Memory Agent。

该组用于回答一个更关键的问题：

> Soul Current State 是否比普通历史摘要更能稳定改变 Agent 行为？

### 9.2 自动化评价器

将人工评估中稳定出现的判断标准转化为 evaluator。

优先自动化：

- 是否重复已否定方案。
- 是否违反明确约束。
- 是否提到关键未解决问题。
- 是否给出可执行下一步。

### 9.3 多轮状态演化实验

第一轮实验主要验证“读取状态是否改变回答”。

后续应验证完整循环：

```text
Initial State
  -> Agent 行动
  -> 新 Evidence
  -> Cognitive Diff
  -> State Patch
  -> Confirm
  -> New State
  -> 下一轮 Agent 行为改变
```

这能验证 Soul 是否真的形成长期认知演化，而不是一次性的上下文注入。

### 9.4 NO_CHANGE 能力实验

Soul 不应把每次互动都转化为状态变化。

后续需要设计专门案例，测试 Reflection 是否能区分：

- 普通任务指令。
- 临时偏好。
- 可丢弃上下文。
- 值得进入 Current State 的稳定项目认知。

### 9.5 Agent 行为回归测试

当 Soul 的状态模型、反射逻辑或 Agent context 格式发生变化时，应复跑行为实验。

这样可以避免工程改动破坏长期连续性、一致性和主动性。

## 10. v0.1 结论

Agent 行为实验是 Soul 从“状态系统”走向“长期协作伙伴”的关键验证。

第一轮实验不需要证明 Soul 已经完整解决 Agent 记忆问题，只需要证明：

- 相同模型下，Current State 会改变 Agent 行为。
- 这种改变能减少重复和约束遗忘。
- 这种改变能让 Agent 更自然地接住项目当前阶段。

如果该假设成立，Soul 的下一步重点应从工程闭环扩展到行为闭环：让每一次被确认的 State Transition 都能在后续 Agent 行动中产生可观察影响。
