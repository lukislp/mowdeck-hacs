# SPDX-FileCopyrightText: 2026 Lukas Körber
# SPDX-License-Identifier: AGPL-3.0-only

# The dev container's base image (devcontainer.json "build"): the tag 3-3.14-bookworm of
# mcr.microsoft.com/devcontainers/python (Python 3.14, which Home Assistant 2026.11 needs),
# pinned by digest. Digest only, because the Dev Container CLI does not parse "tag@digest"; bump
# by looking the tag up again
# (`docker buildx imagetools inspect mcr.microsoft.com/devcontainers/python:3-3.14-bookworm`).
FROM mcr.microsoft.com/devcontainers/python@sha256:0ae003ea761a00b8f8809cfdd08a04edfb1c7f17e76c413b27882ce5939a65f4

# The image ships the Yarn apt repository, whose signing key is no longer accepted
# (NO_PUBKEY 62D54FD4003F6525), so every `apt-get update` fails. Nothing here needs Yarn, so
# remove the source before installing anything.
RUN rm -f /etc/apt/sources.list.d/yarn.list /etc/apt/sources.list.d/yarn.sources \
    && rm -f /usr/share/keyrings/yarn-archive-keyring.gpg /etc/apt/trusted.gpg.d/yarn.gpg

# Home Assistant's camera, stream and dhcp components load these system libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libturbojpeg0 libpcap0.8 \
    && rm -rf /var/lib/apt/lists/*

# uv 0.13.5, the version CI uses (astral-sh/setup-uv in .github/workflows/ci.yml), by digest
# (tag ghcr.io/astral-sh/uv:0.13.5).
COPY --from=ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /usr/local/bin/
