# Contributing to MowDeck for Home Assistant

This is a single-maintainer project, so the process is deliberately small - but it is the same
for every change, including the maintainer's own.

## How changes get in

1. Open an issue first for anything bigger than a typo or an obvious bug fix. Use the templates
   under `.github/ISSUE_TEMPLATE/`.
2. Fork the repository (or branch, with write access) and make your change on a branch.
3. Open a pull request against `main`. `main` is protected: a PR merges only after the required
   checks are green and the branch is up to date; commits must be signed.

## What a pull request needs

- **Conventional Commits.** The version and the release notes are generated from the commit
  messages (`feat:` = minor, `fix:`/`refactor:`/`perf:` = patch, `ci:`/`docs:`/`test:`/`chore:`
  = no release). Squash-merge keeps the PR title as the commit message, so give the PR a
  Conventional Commit title.
- **Green required checks:** `test` (ruff, ruff format, mypy strict, pytest with at least 80 %
  coverage, the HACS layout check), `hassfest`, `hacs` and `review / dependency-review`.
- **Tests for new behaviour** in `tests/`, against the fake MowDeck API in
  `tests/fake_mowdeck.py`. The integration is a pure client of the public MowDeck API; the
  OpenAPI documents in [lukislp/mowdeck](https://github.com/lukislp/mowdeck) (`apps/web/openapi`)
  are its contract, so a new field or command in the fake follows them.
- **Translations** for every new entity, attribute or message in `strings.json`,
  `translations/en.json` and `translations/de.json`.

## Running things locally

Home Assistant's test harness needs Linux (it imports `fcntl`); on Windows run the tests in a
container or WSL.

```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov
uv run python scripts/check_hacs_layout.py
```

## Releases

`.github/workflows/release.yml` releases on every push to `main` whose commits warrant one:
semantic-release computes the version, creates the SSH-signed tag `v<version>` and the GitHub
release, then `scripts/build_release_zip.py` builds `mowdeck.zip` from the tag with the version
in `manifest.json` (the repository's manifest stays at `0.0.0`; HACS installs the zip,
`zip_release` in `hacs.json`). The zip gets `SHA256SUMS`, Sigstore bundles and a build
provenance attestation. A release makes no commit.

**The 0.x line.** The integration stays below 1.0.0 until the owner decides otherwise; on 0.x a
breaking change is a minor release (`.releaserc.json`). The release job refuses to publish while
no `v*` tag exists, because semantic-release would then start at 1.0.0: the line starts from the
initial tag `v0.0.0`, which the owner creates once (signed, on `main`) before the first release.
Going to 1.0.0 is one pull request that removes the `{"breaking": true, "release": "minor"}`
rule and carries a `BREAKING CHANGE:` footer.

**Secrets of the release:** `DEPLOY_SIGNING_KEY` (an ed25519 SSH key whose public half is a
signing key of the owner's account; the job checks it before tagging) and, optionally,
`SEMANTIC_RELEASE_DEPLOY_KEY` (a write deploy key, so the tag is pushed by a key).

**A failed release.** When `publish` fails, the tag and the GitHub release already exist; re-run
the failed job (`gh run rerun <run-id> --failed`). Do not delete or move a published tag.
