# Maintenance Implementation Plan (M, between A and B/C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring GitHub Actions, container images, libraries and Bicep API versions up to date, one commit per area, without changing behavior.

**Architecture:** No new code unless an update needs it. Each task ends with the suites it can break. Deployment waits for plan D.

**Tech Stack:** uv, npm, GitHub Actions, Docker, Bicep.

**Spec:** The product owner's yes on 2026-11-23, and "Latest versions everywhere, including preview" in `docs/decisions.md`.

## Global Constraints

- Nothing released less than 7 days before the task runs. Check the release date, do not guess.
- Out of scope: Azure Search versions and Serverless (own design), chat and planning models, Python 3.13 or later.
- No deployment and no change in Azure. `az bicep build` and `lint` only.
- All ten `uv.lock` files pass `uv lock --check`. Reinstall path dependencies before the api, hosted and contract suites.
- `clients/web` and `clients/dashboard` keep the same versions of shared packages.
- An update that breaks a test is either adapted to in the same commit, or held back and listed with the reason.
- No em-dashes. Commit messages are one plain sentence in the imperative, with the `Co-Authored-By` trailer.

## Review Focus

1. A library that now logs or traces request bodies by default: no photo, question or context in logs (Task 3, the privacy tests of plan A must stay green, and the live run is repeated).
2. A changed shape of streamed output items: carried items still work on both backends (Task 3, live run).
3. An Action whose inputs were renamed: the Pages deploy and the caches still work (Task 1, read the release notes of every major version passed).

---

### Task 1: GitHub Actions
- Modify: `.github/workflows/tests.yml`, `.github/workflows/pages.yml`
- [ ] Read the release notes of every major version between the pinned and the newest (checkout, setup-node, setup-uv, upload-pages-artifact, deploy-pages). List input changes that touch us.
- [ ] Update to the newest major that is old enough. Run `uvx zizmor .github/workflows` or `actionlint` offline if available. Add a guard or retry for the Pages artifact fault seen on 2026-11-23 only if the new versions do not handle it.
- [ ] Commit `Update the GitHub Actions`

### Task 2: Container images
- Modify: `api/Dockerfile`, `agents/hosted/Dockerfile`, `services/dealer/Dockerfile`
- [ ] Python `3.12.16-slim` and uv `0.13.9` (or the newest old enough). Check that the uv version reads lock revision 5 and that `uv sync --frozen --no-dev` is unchanged. Build all three locally with Docker for linux/amd64 and run `python -c "import app.main"` or the equivalent in each.
- [ ] Commit `Update Python and uv in the container images`

### Task 3: Python libraries
- Modify: the ten `uv.lock` files, and `pyproject.toml` only where a bound must move
- [ ] Upgrade with `--upgrade-package name==version` for versions that are old enough. Keep shared packages on one version in all projects.
- [ ] Run every Python suite with `--locked`, warnings included. `pip-audit` on the exports: no known vulnerabilities.
- [ ] The controller repeats the live photo run on the own loop's model and the prompt agent.
- [ ] Commit `Update the Python libraries`

### Task 4: npm packages
- Modify: `clients/web/package.json`, `clients/dashboard/package.json` and both lock files
- [ ] `@azure/msal-browser`, `vite`, `vitest`, `@vitejs/plugin-react`. Test and build both, `npm audit` clean, no drift between the two.
- [ ] Commit `Update the npm packages`

### Task 5: Bicep API versions
- Modify: `infra/**/*.bicep`, `docs/decisions.md` (the version table)
- [ ] `Microsoft.CognitiveServices/*` `2026-11-15-preview`, `Microsoft.ContainerRegistry/registries` `2026-11-01`, `Microsoft.Web/staticSites` `2025-05-01`, `Microsoft.Resources/resourceGroups` `2026-06-01`. Keep Search. Read the change notes for properties we use. `sh infra/tests/test_bicep.sh` and `uvx pytest infra/tests`. A provision preview waits for plan D.
- [ ] Commit `Update the Bicep API versions`
