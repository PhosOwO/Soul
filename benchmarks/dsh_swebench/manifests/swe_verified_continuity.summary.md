# Verified Manifest Summary

- Generated: `2026-09-10T10:14:01Z`
- Source dataset: `princeton-nlp/SWE-bench_Verified`
- Source split: `test`
- Source rows available: `500`
- Selected rows: `9`
- Selection seed: `soulkit-v0`
- Continuity mode: `True`

## Repository Distribution

| Repo | Count |
| --- | ---: |
| `matplotlib/matplotlib` | 3 |
| `scikit-learn/scikit-learn` | 3 |
| `sphinx-doc/sphinx` | 3 |

## Instances

| # | Instance | Repo | Summary | F2P | P2P | Difficulty |
| ---: | --- | --- | --- | ---: | ---: | --- |
| 1 | `scikit-learn__scikit-learn-10297` | `scikit-learn/scikit-learn` | linear_model.RidgeClassifierCV's Parameter store_cv_values issue | 1 | 28 | `15 min - 1 hour` |
| 2 | `scikit-learn__scikit-learn-10844` | `scikit-learn/scikit-learn` | fowlkes_mallows_score returns RuntimeWarning when variables get too big | 1 | 16 | `15 min - 1 hour` |
| 3 | `scikit-learn__scikit-learn-10908` | `scikit-learn/scikit-learn` | CountVectorizer's get_feature_names raise not NotFittedError when the vocabulary parameter is provided | 1 | 47 | `15 min - 1 hour` |
| 4 | `matplotlib__matplotlib-13989` | `matplotlib/matplotlib` | hist() no longer respects range=... when density=True | 1 | 411 | `<15 min fix` |
| 5 | `matplotlib__matplotlib-14623` | `matplotlib/matplotlib` | Inverting an axis using its limits does not work for log scale | 1 | 400 | `15 min - 1 hour` |
| 6 | `matplotlib__matplotlib-20488` | `matplotlib/matplotlib` | test_huge_range_log is failing... | 1 | 111 | `15 min - 1 hour` |
| 7 | `sphinx-doc__sphinx-10323` | `sphinx-doc/sphinx` | Use of literalinclude prepend results in incorrect indent formatting for code eamples | 1 | 40 | `<15 min fix` |
| 8 | `sphinx-doc__sphinx-10435` | `sphinx-doc/sphinx` | LaTeX: new Inline code highlighting from #10251 adds whitespace at start and end in pdf output | 1 | 74 | `<15 min fix` |
| 9 | `sphinx-doc__sphinx-10449` | `sphinx-doc/sphinx` | `autodoc_typehints = "description"` causes autoclass to put a return type | 1 | 30 | `<15 min fix` |

## Policy

These rows are selected from an external benchmark dataset. They are not locally invented tasks.
The manifest intentionally omits gold patches, test patches, and hints from the agent prompt path.
