# DSH SWE-bench Runner

This directory contains the runner skeleton for the DSH + SoulKit benchmark.
It is orchestration code: external benchmark instances go in, DSH runs each
experiment arm, patches and logs come out, and official benchmark evaluators
remain the correctness oracle.

The runner must not define benchmark tasks from scratch. Reported tasks should
come from existing external datasets such as SWE-bench Lite, SWE-bench Verified,
BugsInPy, or Defects4J.

## Current Scope

`run_dsh_swebench.py` currently supports:

- JSONL manifest loading and validation
- `baseline`, `capture_only`, and `soul_active` arm planning
- generated per-arm Cordis patch files
- isolated artifact directory layout
- DSH `headless` command construction
- `--dry-run` validation
- per-arm workspace preparation from external repository `base_commit`
- SWE-bench-style prediction files for collected patches
- official SWE-bench evaluator invocation through `--evaluate`

## DSH Requirement

Use Node 22 for DSH. On this machine the reliable invocation prefix is:

```bash
PATH=/Users/bytedance/.nvm/versions/node/v22.23.1/bin:$PATH
```

The expected DSH entrypoint is:

```bash
dsh --profile headless "<task>"
```

The SoulKit patch is passed with:

```bash
dsh --profile headless --patch <generated-soul-patch.yml> "<task>"
```

Before running a benchmark on a new machine, verify that the selected DSH
profile has credentials configured:

```bash
dsh --profile headless "Reply exactly: ok"
```

## Manifest

Each manifest row maps to one external benchmark instance.

```json
{
  "instance_id": "django__django-12345",
  "dataset": "swe-bench-verified",
  "repo": "django/django",
  "base_commit": "abc123",
  "problem_statement": "...",
  "test_command": "official-swebench",
  "group_id": "django__django-12345",
  "sequence_index": 0,
  "evaluation": {
    "type": "swebench",
    "instance_id": "django__django-12345"
  }
}
```

For independent runs, set `group_id` to `instance_id`. For continuity runs,
set `group_id` to the external repository or project ID and use
`sequence_index` for the deterministic task order.

## Dry Run

Validate a manifest and print planned DSH commands:

```bash
python3 benchmarks/dsh_swebench/run_dsh_swebench.py \
  --manifest benchmarks/dsh_swebench/manifests/swe_lite_smoke.example.jsonl \
  --arms baseline,soul_active \
  --output-dir benchmarks/dsh_swebench/results/smoke_dry_run \
  --dry-run
```

## Generate A SWE-bench Manifest

Generate a candidate SWE-bench Lite batch manifest from external HuggingFace
dataset metadata:

```bash
python3 benchmarks/dsh_swebench/make_swebench_manifest.py \
  --dataset lite \
  --limit 10 \
  --seed soulkit-v0 \
  --output benchmarks/dsh_swebench/manifests/swe_lite_batch10.jsonl \
  --summary benchmarks/dsh_swebench/manifests/swe_lite_batch10.summary.md
```

Generate a repository-grouped continuity candidate:

```bash
python3 benchmarks/dsh_swebench/make_swebench_manifest.py \
  --dataset verified \
  --continuity \
  --repos 3 \
  --per-repo 3 \
  --seed soulkit-v0 \
  --output benchmarks/dsh_swebench/manifests/swe_verified_continuity.jsonl \
  --summary benchmarks/dsh_swebench/manifests/swe_verified_continuity.summary.md
```

The generator records only public task metadata needed by the runner. It does
not pass gold patches, test patches, or hints into the agent prompt.

## Real Run Shape

Once workspaces are prepared:

```bash
python3 benchmarks/dsh_swebench/run_dsh_swebench.py \
  --manifest benchmarks/dsh_swebench/manifests/swe_lite_batch10.jsonl \
  --arms baseline,soul_active \
  --workspace-root /path/to/prepared/workspaces \
  --output-dir benchmarks/dsh_swebench/results/swe_lite_batch10_compare_1 \
  --timeout-seconds 1800
```

Or let the runner prepare per-arm workspaces by cloning each external
repository and checking out `base_commit`:

```bash
python3 benchmarks/dsh_swebench/run_dsh_swebench.py \
  --manifest benchmarks/dsh_swebench/manifests/swe_lite_batch10.jsonl \
  --arms baseline,soul_active \
  --prepare-workspaces \
  --output-dir benchmarks/dsh_swebench/results/swe_lite_batch10_compare_1
```

Run the official SWE-bench evaluator after predictions are written:

```bash
python3 benchmarks/dsh_swebench/run_dsh_swebench.py \
  --manifest benchmarks/dsh_swebench/manifests/swe_lite_batch10.jsonl \
  --arms baseline,soul_active \
  --prepare-workspaces \
  --evaluate \
  --swebench-dataset-name SWE-bench/SWE-bench_Lite \
  --eval-max-workers 1 \
  --eval-timeout 1800 \
  --output-dir benchmarks/dsh_swebench/results/swe_lite_batch10_compare_1
```

The runner writes:

```text
results/<run_id>/
  run_metadata.json
  manifest.jsonl
  report.md
  predictions/
    baseline.json
    baseline.jsonl
    soul_active.json
    soul_active.jsonl
  instances/
    <instance_id>/
      <arm>/
        soul.patch.yml
        patch.diff
        stdout.log
        stderr.log
        metrics.json
        soul_state_before/
        soul_state_after/
```

The `.json` prediction files are the official SWE-bench evaluator input. The
`.jsonl` files are auxiliary line-delimited copies for inspection.

Official evaluation requires the `swebench` Python package and Docker. If those
are not available, use `--dry-run` and workspace preparation first, then run
evaluation in an environment with Docker access.

The runner stores HuggingFace and datasets cache under
`<output-dir>/evaluator_cache` during evaluator runs so benchmark artifacts stay
self-contained and do not depend on writing to a user-global cache directory.

## Workspace Preparation

With `--prepare-workspaces`, the runner creates one isolated checkout per
`instance_id / arm`:

```text
results/<run_id>/workspaces/<instance_id>/<arm>/
```

It uses a mirror clone cache:

```text
results/<run_id>/repo_cache/<repo>.git
```

By default repositories are cloned from:

```text
https://github.com/<repo>.git
```

A manifest row may override that with `repo_url`, which is useful for local
smoke tests or alternate mirrors. Existing workspaces are reused unless
`--force-prepare` is passed.

## Evaluation

With `--evaluate`, the runner invokes the official evaluator once per arm:

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name SWE-bench/SWE-bench_Lite \
  --split test \
  --predictions_path <results>/<run_id>/predictions/<arm>.json \
  --run_id <run_id>-<arm> \
  --instance_ids <manifest instance ids...>
```

The runner checks for the `swebench` Python package and Docker before invoking
evaluation. It does not implement its own correctness grading.

## Resume Without Rerunning DSH

If DSH has already produced patches and only official evaluation needs to be
retried, add `--resume`. Existing `instances/<id>/<arm>/metrics.json` files
cause the runner to skip the DSH solving phase and reuse the existing patches:

```bash
python3 benchmarks/dsh_swebench/run_dsh_swebench.py \
  --manifest benchmarks/dsh_swebench/manifests/swe_lite_batch10.jsonl \
  --arms baseline,soul_active \
  --prepare-workspaces \
  --resume \
  --evaluate \
  --swebench-dataset-name SWE-bench/SWE-bench_Lite \
  --eval-max-workers 1 \
  --eval-timeout 1800 \
  --output-dir benchmarks/dsh_swebench/results/swe_lite_batch10_compare_1
```

On Apple Silicon with Colima, SWE-bench's published eval images may be
`linux/amd64` only. If Docker reports `no matching manifest for linux/arm64`,
pre-pull the target image with:

```bash
docker pull --platform linux/amd64 <swebench-eval-image>
```

Prefer small batches on disk-constrained machines:

1. Pull one missing eval image with `docker pull --platform linux/amd64 ...`.
2. Run `python -m swebench.harness.run_evaluation` for one `--instance_ids`
   value and one arm.
3. Repeat for the paired arm.
4. Remove the completed eval image with `docker image rm ...`.
5. Check `df -h` before pulling the next image.

This avoids filling the Colima Docker disk and corrupting containerd snapshots.

## Result Interpretation

The current `soul_active` runner configuration isolates each benchmark instance
in its own `soul_project`. That is good for arm isolation, but it means Soul
does not learn across tasks in this run shape. The DSH plugin enqueues evidence
and may update Working State, but accepted state changes still require an
explicit review/apply step. A benchmark intended to measure Soul's accumulated
memory effect should add a shared-state mode plus post-run reflection and a
controlled promotion policy.

## Next Steps

1. Add an aggregate comparison report across arms.
2. Add shared-state benchmark mode for continuity experiments.
3. Add an explicit post-run reflection/promotion phase before claiming a Soul
   memory effect.
