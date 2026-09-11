# Lite Manifest Summary

- Generated: `2026-09-10T10:13:58Z`
- Source dataset: `princeton-nlp/SWE-bench_Lite`
- Source split: `test`
- Source rows available: `300`
- Selected rows: `10`
- Selection seed: `soulkit-v0`
- Continuity mode: `False`

## Repository Distribution

| Repo | Count |
| --- | ---: |
| `sympy/sympy` | 4 |
| `django/django` | 3 |
| `astropy/astropy` | 1 |
| `psf/requests` | 1 |
| `pytest-dev/pytest` | 1 |

## Instances

| # | Instance | Repo | Summary | F2P | P2P | Difficulty |
| ---: | --- | --- | --- | ---: | ---: | --- |
| 1 | `django__django-15202` | `django/django` | URLField throws ValueError instead of ValidationError on clean | 2 | 6 | `n/a` |
| 2 | `sympy__sympy-17655` | `sympy/sympy` | Unexpected exception when multiplying geometry.Point and number | 2 | 9 | `n/a` |
| 3 | `sympy__sympy-13647` | `sympy/sympy` | Matrix.col_insert() no longer seems to work correctly. | 1 | 76 | `n/a` |
| 4 | `astropy__astropy-14365` | `astropy/astropy` | ascii.qdp Table format assumes QDP commands are upper case | 1 | 8 | `n/a` |
| 5 | `psf__requests-2317` | `psf/requests` | method = builtin_str(method) problem | 8 | 133 | `n/a` |
| 6 | `sympy__sympy-23262` | `sympy/sympy` | Python code printer not respecting tuple with one element | 1 | 61 | `n/a` |
| 7 | `django__django-12184` | `django/django` | Optional URL params crash some view functions. | 1 | 25 | `n/a` |
| 8 | `sympy__sympy-17022` | `sympy/sympy` | Lambdify misinterprets some matrix expressions | 1 | 8 | `n/a` |
| 9 | `pytest-dev__pytest-5692` | `pytest-dev/pytest` | Hostname and timestamp properties in generated JUnit XML reports | 2 | 68 | `n/a` |
| 10 | `django__django-12856` | `django/django` | Add check for fields of UniqueConstraints. | 3 | 78 | `n/a` |

## Policy

These rows are selected from an external benchmark dataset. They are not locally invented tasks.
The manifest intentionally omits gold patches, test patches, and hints from the agent prompt path.
