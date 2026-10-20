# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's private vulnerability reporting:
https://github.com/lukislp/mowdeck-hacs/security/advisories/new

Do not open a public issue for security problems. You will get an acknowledgement within a few
days and a fix or mitigation plan before any public disclosure. A problem in the MowDeck services
themselves belongs to https://github.com/lukislp/mowdeck/security/advisories/new.

## Supported versions

Only the latest release and the `main` branch receive security fixes.

## How the integration handles secrets

- The MowDeck API token is stored in the Home Assistant config entry and is only sent to the
  configured MowDeck URL, in the token exchange (`POST /api/v1/identity/token`). Every other
  request carries a short-lived access token.
- Diagnostics redact the API token, access tokens, connection options, serials, vendor ids,
  positions and map geometry.
- The integration has no Python requirements of its own and installs nothing at runtime.

## Verifying a release

Release tags are SSH-signed. The release asset `mowdeck.zip` (what HACS installs) comes with
`SHA256SUMS`, a Sigstore bundle per file and a GitHub build provenance attestation; the
commands are in each release's notes, for example
`gh attestation verify mowdeck.zip -R lukislp/mowdeck-hacs`.
