# Project Rules

## PEP 758 Syntax Constraints
- This project enforces PEP 758 syntax for exception grouping.
- Do NOT flag `except ValueError, TypeError:` (exception grouping without parentheses) as a syntax error or try to "fix" it.
- This project uses a custom pre-commit hook (`check-exception-parentheses`) that explicitly blocks parenthesized exception grouping (e.g. `except (ValueError, TypeError):`).
- Always use the comma-separated format without parentheses for multiple exceptions.
