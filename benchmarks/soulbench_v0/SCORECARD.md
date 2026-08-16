# SoulBench v0 AMBench-Style Scorecard

Run source: `benchmarks/soulbench_v0/results/deepseek_latest/results.json`

Rescored output: `benchmarks/soulbench_v0/results/deepseek_latest_rescored`

Backend: `deepseek`

Model: `deepseek-chat`

Task count: `12`

Adapters:

- `baseline`: current task only.
- `memory_summary`: current task plus plain natural-language memory summary.
- `soul`: current task plus Soul projected Current State generated through `Evidence -> Patch Proposal -> Review/Apply -> Projected State`.

## Overall

| Adapter | Quick Score | Judge Score | Forbidden Hits | Avg Latency ms | Total Tokens |
|---|---:|---:|---:|---:|---:|
| `baseline` | 0.5417 | 0.4167 | 0 | 3006.462 | 3532 |
| `memory_summary` | 0.8542 | 0.7979 | 0 | 1899.749 | 2984 |
| `soul` | 0.9583 | 0.8833 | 0 | 1818.204 | 4640 |

## Lift

| Comparison | Judge Delta | Relative Lift | Quick Delta | Token Delta |
|---|---:|---:|---:|---:|
| `soul` vs `baseline` | +0.4666 | +112.0% | +0.4166 | +1108 |
| `soul` vs `memory_summary` | +0.0854 | +10.7% | +0.1041 | +1656 |

## Category Scores

| Category | Cases | Baseline | Memory Summary | Soul | Soul vs Memory |
|---|---:|---:|---:|---:|---:|
| `coding_project_memory` | 2 | 0.3500 | 0.8375 | 1.0000 | +0.1625 |
| `research_experiment_continuity` | 3 | 0.4000 | 0.8333 | 0.9000 | +0.0667 |
| `product_decision_constraints` | 3 | 0.5167 | 0.8417 | 0.8417 | +0.0000 |
| `stale_or_conflicting_memory` | 3 | 0.4000 | 0.6250 | 0.7917 | +0.1667 |
| `priority_and_projection` | 1 | 0.3500 | 1.0000 | 1.0000 | +0.0000 |

## Case Scores

| Case | Category | Baseline | Memory Summary | Soul | Winner |
|---|---|---:|---:|---:|---|
| `coding_auth_001` | `coding_project_memory` | 0.350 | 1.000 | 1.000 | tie |
| `coding_api_002` | `coding_project_memory` | 0.350 | 0.675 | 1.000 | soul |
| `research_focal_001` | `research_experiment_continuity` | 0.500 | 0.825 | 1.000 | soul |
| `research_backbone_002` | `research_experiment_continuity` | 0.350 | 0.850 | 0.850 | tie |
| `research_mld_003` | `research_experiment_continuity` | 0.350 | 0.825 | 0.850 | soul |
| `product_pricing_001` | `product_decision_constraints` | 0.525 | 0.850 | 0.850 | tie |
| `product_onboarding_002` | `product_decision_constraints` | 0.350 | 0.675 | 0.675 | tie |
| `product_enterprise_003` | `product_decision_constraints` | 0.675 | 1.000 | 1.000 | tie |
| `stale_cache_001` | `stale_or_conflicting_memory` | 0.350 | 0.500 | 1.000 | soul |
| `stale_security_002` | `stale_or_conflicting_memory` | 0.350 | 0.700 | 0.700 | tie |
| `stale_ops_003` | `stale_or_conflicting_memory` | 0.500 | 0.675 | 0.675 | tie |
| `mixed_priority_001` | `priority_and_projection` | 0.350 | 1.000 | 1.000 | tie |

## Interpretation

Soul improves most clearly when the useful context is not just a fact to remember, but a typed boundary:

- rejected direction: do not repeat a known bad path;
- active constraint: preserve current project constraints;
- stale/conflicting memory: prefer confirmed state over earlier suspicion;
- open question or needs-review item: do not promote uncertainty into accepted state.

Plain memory summary is already strong when the memory note is explicit and short. In this run, it ties Soul on product-scope cases where the summary itself contains the key decision boundary. Soul's current advantage is therefore not universal recall; it is stronger state discipline under ambiguity, stale evidence, and typed constraints.

## Cost Note

Soul used more prompt tokens than `memory_summary` in this run: `4640` vs `2984`, or `+1656` total tokens across 12 cases. The scoring gain over `memory_summary` was `+0.0854` judge score. This is the main efficiency tradeoff to optimize next: keep the typed state advantage while reducing injected projected-state size.
