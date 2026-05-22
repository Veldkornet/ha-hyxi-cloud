## 2025-02-21 - Memory allocations in Python default arguments
**Learning:** Passing a dictionary or list default argument like `.get("key", {})` triggers a redundant evaluation of that object. This implies the creation and immediate deletion of an empty dictionary inside memory when the default parameter isn't used.
**Action:** Use an explicit `or {}` instead of passing the default argument, i.e., `.get("key") or {}`.

## 2026-05-22 - PEP 758 Exception Syntax in Python 3.14+
**Learning:** PEP 758 (introduced in Python 3.14) allows omitting parentheses when catching multiple exceptions in `except` and `except*` clauses (e.g. `except ValueError, TypeError:`), provided the `as` clause is not used. However, this syntax is not backward-compatible and will cause syntax errors in Python 3.13 and earlier.
**Action:** Always omit the parentheses, e.g. `except ValueError, TypeError:`, since we are using Python 3.14 runtimes.
