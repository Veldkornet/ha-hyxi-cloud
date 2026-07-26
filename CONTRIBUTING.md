# Contributing to HYXI Cloud

First off, thank you for considering contributing! People like you make the Home Assistant community great. ☀️

## 🚀 How Can I Help?

### 1. Testing "Untested" Devices
If you own hardware marked as `⚠️ Untested` in the README, we need your data!
- Enable **Debug Logging** for the integration.
- Open an Issue with a sanitized (ID/Serial removed) snippet of the API response.
- This helps us map the correct sensors for everyone.

### 2. Reporting Bugs
- Use the **Bug Report** template.
- Include your Home Assistant version and any relevant logs.
- Check if the issue has already been reported!

### 3. Suggesting Enhancements
- Open an Issue labeled `enhancement` to discuss your idea before writing code.

---

## 🛠️ Development Setup

This project uses modern CI/CD to keep the code clean and secure.

1. **Fork and Clone:** Create a branch from `main`.
2. **Coding Standards:** We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting.
3. **Security:** Every Pull Request is scanned by **CodeQL**, **Gitleaks**, and **Bandit**.
   - *Note: PRs containing hardcoded secrets or insecure Python patterns will be blocked.*
4. **Testing:** If you add a new sensor, ensure it has a `device_class`, `state_class`, and appropriate units.

## 🔖 Releasing (Version Bumps)

`manifest.json`'s `version` field is the only version string you ever edit by hand. Everything else derives from it automatically:
- `const.py`'s `VERSION` reads `manifest.json` directly at import time.
- `pyproject.toml`'s `version` is auto-corrected by the `sync-versions` pre-commit hook (`scripts/sync_versions.py`) whenever it drifts from `manifest.json`.

Similarly, `pyproject.toml`'s `dependencies` list is the only runtime-dependency list you edit by hand — `manifest.json`'s `requirements` array is auto-generated from it by the same hook. Just run `pre-commit run --all-files` (or commit and let pre-commit.ci do it) after bumping either file; if it rewrites something, re-stage and commit again.

## ⚖️ License
By contributing, you agree that your contributions will be licensed under the project's **MIT License**.

---
**CPE Identifier:** `cpe:2.3:a:veldkornet:ha-hyxi-cloud:*:*:*:*:*:home_assistant:*:*`
