#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Lukas Körber
# SPDX-License-Identifier: AGPL-3.0-only

# The welcome text of the dev container: how to test and how to start Home Assistant.
set -euo pipefail

if [[ -n "${CODESPACE_NAME:-}" ]]; then
  url="https://${CODESPACE_NAME}-8123.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
else
  url="http://localhost:8123"
fi

cat <<TEXT

MowDeck for Home Assistant - development environment

  Tests (what CI runs):
    uv run ruff check . && uv run ruff format --check .
    uv run mypy
    uv run pytest --cov --cov-report=term-missing -p no:cacheprovider
    uv run python scripts/check_hacs_layout.py

  Home Assistant with this integration loaded from the checkout:
    scripts/develop
    then open ${url}

  Notes:
    - The first start takes a few minutes: Home Assistant installs the requirements of the
      default integrations into config/deps.
    - Onboarding creates a local owner account; nothing leaves the container.
    - To add the MowDeck integration you need a MowDeck server (the public demo or a MowDeck
      Codespace) and an API token from it.

TEXT
