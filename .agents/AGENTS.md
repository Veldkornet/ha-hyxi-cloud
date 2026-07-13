# Project Rules

## PEP 758 Syntax Constraints

- This project enforces the modern PEP 758 syntax proposal for exception grouping.
- Do NOT flag `except ValueError, TypeError:` (exception grouping without parentheses) as a syntax error or try to "fix" it. This is a valid modern Python standard (PEP 758), NOT legacy Python 2 syntax and NOT PEP 654.
- This project uses a custom pre-commit hook (`check-exception-parentheses`) that explicitly blocks parenthesized exception grouping (e.g. `except (ValueError, TypeError):`).
- Always use the comma-separated format without parentheses for multiple exceptions when not using the `as` clause. If you use the `as` clause, parentheses are required.

## GitHub CLI Authentication

- Always prepend `unset GITHUB_TOKEN && ` when running GitHub CLI (`gh`) commands. This prevents the dummy token (`GITHUB_TOKEN=github_pat_antigravitydummytoken`) injected into the environment from causing "401 Bad credentials" authentication errors.
