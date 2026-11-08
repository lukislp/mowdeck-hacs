#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Lukas Körber
# SPDX-License-Identifier: AGPL-3.0-only

# Installs the locked development environment (Home Assistant, the test harness, ruff, mypy)
# the same way CI does (`uv sync --locked`). Codespaces runs this in the prebuild and again
# when a codespace opens on a prebuild that is older than the branch, so it has to be safe to
# repeat. The repository has no pre-commit configuration, so there is no hook to install.
set -euo pipefail

cd "$(dirname "$0")/.."

uv sync --locked
