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

### Choosing a version bump size

[Release Drafter](.github/release-drafter.yml) keeps a draft release up to date on every push to `main`, resolving the next version number from your PR's label — see the `version-resolver` block in that file for the current label-to-bump mapping.

**Label your PR accordingly**, especially `new-feature` for anything adding real functionality — without it, both drafts below silently fall back to a patch bump regardless of what the PR actually does. When you bump `manifest.json` by hand, match whichever draft's `$RESOLVED_VERSION` you're about to publish (see below) — no suffix-stripping needed, each draft already resolves the version correctly for its own track.

### Two release drafts: beta and stable

The [Release Drafter workflow](.github/workflows/release-drafter.yml) maintains **two separate drafts** on every push to `main`, both from the same [config](.github/release-drafter.yml):

- **Update Beta Draft** — a pre-release (tagged `vX.Y.Z-beta.N`), since whatever was last *published* (beta or stable). Publishing it as-is ships a beta to HACS users who've opted into the beta channel — see the README's Installation section. The draft keeps proposing the same version until you publish it, not on every push. Once published, the *next* draft either bumps `beta.N` to `beta.N+1` (if the last published release was itself a beta) or starts a fresh `beta.0` on the next version (if the last published release was stable).
- **Update Stable Draft** — a regular release (no suffix), since the last *stable* release specifically, accumulating across however many betas happened in between. Publishing it ships to everyone and marks it "latest". Its version and changelog are already correct for a clean stable cut — no editing needed, just publish.

Both are always live as separate drafts in the repo's Releases page; which one you publish depends on whether you're shipping a beta or a stable release.

## ⚖️ License
By contributing, you agree that your contributions will be licensed under the project's **MIT License**.

---
**CPE Identifier:** `cpe:2.3:a:veldkornet:ha-hyxi-cloud:*:*:*:*:*:home_assistant:*:*`
