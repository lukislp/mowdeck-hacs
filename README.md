# MowDeck for Home Assistant

Custom integration (HACS) that brings the mowers of a [MowDeck](https://github.com/lukislp/mowdeck) installation into Home Assistant. It is a pure client of the public MowDeck API: it authenticates with an API token, reads mowers, states, capabilities and maps over HTTP, receives live states over the MowDeck Realtime WebSocket and sends commands through Fleet. It needs no Python packages beyond Home Assistant itself.

- Domain `mowdeck`, IoT class `local_push`, integration type `hub` (one entry per MowDeck account, one device per mower).
- Requires Home Assistant 2026.10.0 or newer (`hacs.json`).
- Licence: AGPL-3.0-only, like the rest of MowDeck.
- Status: private repository; HACS installation works once it is public.

## Installation

HACS: three dots, **Custom repositories**, add `https://github.com/lukislp/mowdeck-hacs` with type **Integration** (possible once the repository is public), install **MowDeck**, restart Home Assistant.

Manually: copy `custom_components/mowdeck` into `<config>/custom_components/` and restart Home Assistant.

## Create an API token in MowDeck

In the MowDeck web app: **Settings, API tokens, New token**. Give it a name ("Home Assistant"), the level (the whole account, or a site, group or single mower) and the permissions:

| Permission | Needed for |
| --- | --- |
| `mower.view` | required: mowers, states, map, live updates |
| `mower.control` | start, pause, dock, blade height, speed (without it Home Assistant is read only; the emergency stop works for viewers too) |
| `mower.drive` | optional, not used by the integration today (joystick driving) |

The secret (`mdk_...`) is shown once. Through the API: `POST /api/v1/identity/accounts/{account_id}/api-tokens` with `{"name": "Home Assistant", "grants": [{"level": {"type": "account", "id": "<account id>"}, "permissions": ["mower.view", "mower.control"]}]}`.

## Configuration

**Settings, Devices and services, Add integration, MowDeck**:

| Field | Value |
| --- | --- |
| URL | the base URL of the MowDeck web app, for example `https://mowdeck.example.ts.net` or `http://192.168.1.20` |
| API token | the token from above |

The flow exchanges the token, reads `/api/v1/identity/me` and lists the mowers; the entry is keyed by the MowDeck account id, so the same account cannot be added twice.

**Options** (Configure on the entry): the poll interval while the live connection is down (default 60 s, 15 to 3600) and whether to show the emergency stop button (default on).

## Entities

Per mower one device (manufacturer Mammotion, model from the capabilities, firmware as software version, a link to the mower page of the web app):

| Entity | Example id | Source |
| --- | --- | --- |
| Lawn mower | `lawn_mower.luba` | activity: `mowing`, `paused`, `returning`, `docked` (also while charging), `idle`, `error`; start, pause, dock |
| Battery | `sensor.luba_battery` | `battery_percent` |
| Activity | `sensor.luba_activity` | the MowDeck activity (enum, includes `charging` and `offline`) |
| RTK fix, RTK source, Connection | `sensor.luba_rtk_fix`, ... | enums (diagnostic) |
| Blade height, Speed | `sensor.luba_blade_height`, `sensor.luba_speed` | mm, m/s |
| Last seen | `sensor.luba_last_seen` | `observed_at` (diagnostic) |
| Error code | `sensor.luba_error_code` | `error_code`, attribute `error_message` (diagnostic) |
| Charging, Error | `binary_sensor.luba_charging`, `binary_sensor.luba_error` | |
| Online | `binary_sensor.luba_online` | a fresh state (not older than 5 minutes, not `offline`) |
| Emergency stop, Return to dock | `button.luba_emergency_stop`, `button.luba_return_to_dock` | the emergency stop can be hidden in the options |
| Blade height, Mowing speed | `number.luba_blade_height`, `number.luba_mowing_speed` | ranges from the mower's capabilities |
| Area | `select.luba_area` | `all` or one map area, named as in MowDeck (the name given in MowDeck, else the mower's own name, else "Area N" / "Bereich N" in map order); used by "start mowing" |
| Position | `device_tracker.luba_position` | GPS (`position` of the state) |
| Camera light | `switch.luba_camera_light` | the camera's headlamp: `light_on` of the state (unknown while not reported), `set_light`; only for mowers whose capabilities have `light` |

**Start mowing** resumes a paused job; otherwise it starts the area chosen in the Area select, or every area of the map. A select holds one value, so several areas go through the action `mowdeck.start_mow`.

The event `mowdeck_command_result` carries every command status change (`mower_id`, `command_id`, `status`, `reason`).

## Actions

Targets are MowDeck lawn mower entities.

| Action | Fields | API command |
| --- | --- | --- |
| `mowdeck.start_mow` | `area_ids` (list, optional; default: the selection), `pattern` (object, optional) | `start_mow` |
| `mowdeck.emergency_stop` | | `emergency_stop` |
| `mowdeck.set_blade_height` | `mm` (10 to 120) | `set_blade_height` |
| `mowdeck.set_speed` | `mps` (up to 2) | `set_speed` |

```yaml
action: mowdeck.start_mow
target:
  entity_id: lawn_mower.luba
data:
  area_ids: ["zone-front", "zone-back"]
  pattern: {name: stripes, angle_deg: 45}
```

## Troubleshooting

- **"MowDeck live updates are refused" (Repairs)**: the Realtime WebSocket closes with 4401 even with a fresh access token. Check that the token still exists and has `mower.view`. Polling continues meanwhile; the issue disappears when the socket works again.
- **Reauthentication requested**: the API token was revoked, expired or its owner disabled. Create a new token and enter it in the reauth dialog (it must belong to the same account).
- **Cannot connect**: use the URL that Home Assistant itself can reach (the Docker network, the LAN address, or the Tailscale name). Behind Tailscale, the Home Assistant host must be in the tailnet; MagicDNS names such as `https://mowdeck.<tailnet>.ts.net` work once HTTPS certificates are enabled for the tailnet.
- **HTTPS**: with `https://` the WebSocket uses `wss://` on the same host. Self-signed certificates are not accepted; use Tailscale HTTPS or a certificate from a public CA.
- **Commands fail with "may not send"**: the token lacks `mower.control` on that mower.
- **Diagnostics** (three dots on the entry) contain mowers, states, capabilities and the realtime status; the API token, connection options, serials and positions are redacted.

## Development

This repository is the home of the integration; it was moved here from the MowDeck monorepo (`integrations/home-assistant`, see `CHANGELOG.md`). The integration is a pure client of the public MowDeck API; the OpenAPI document of [lukislp/mowdeck](https://github.com/lukislp/mowdeck) is its contract. Home Assistant's test harness needs Linux or WSL (it imports `fcntl`); on Windows only lint and mypy run.

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov
uv run python scripts/check_hacs_layout.py
```

CI (`.github/workflows/ci.yml`) runs the same plus hassfest.

## Licence

AGPL-3.0-only, see `LICENSE`.
