# MowDeck for Home Assistant

[![CI](https://github.com/lukislp/mowdeck-hacs/actions/workflows/ci.yml/badge.svg)](https://github.com/lukislp/mowdeck-hacs/actions/workflows/ci.yml) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lukislp/mowdeck-hacs/badge)](https://scorecard.dev/viewer/?uri=github.com/lukislp/mowdeck-hacs) [![CodeQL](https://github.com/lukislp/mowdeck-hacs/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/lukislp/mowdeck-hacs/security/code-scanning)
[![Release](https://img.shields.io/github/v/release/lukislp/mowdeck-hacs)](https://github.com/lukislp/mowdeck-hacs/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz/docs/faq/custom_repositories/)
[![License: AGPL-3.0](https://img.shields.io/github/license/lukislp/mowdeck-hacs)](LICENSE)

Custom integration (HACS) that brings the mowers of a [MowDeck](https://github.com/lukislp/mowdeck) installation into Home Assistant. It is a pure client of the public MowDeck API: it authenticates with an API token, reads mowers, states, capabilities and maps over HTTP, receives live states over the MowDeck Realtime WebSocket and sends commands through Fleet. It needs no Python packages beyond Home Assistant itself.

- Domain `mowdeck`, IoT class `local_push`, integration type `hub` (one entry per MowDeck account, one device per mower).
- Requires Home Assistant 2026.10.0 or newer (`hacs.json`).
- [Integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/): Silver (`custom_components/mowdeck/quality_scale.yaml`; tests keep the coverage above 95 %).
- Licence: AGPL-3.0-only, like the rest of MowDeck.
- Releases: semantic versions on the 0.x line, SSH-signed tags, `mowdeck.zip` with Sigstore signatures and build provenance (see `SECURITY.md`).

## Installation

HACS: three dots, **Custom repositories**, add `https://github.com/lukislp/mowdeck-hacs` with type **Integration**, install **MowDeck**, restart Home Assistant. HACS installs the `mowdeck.zip` of the chosen release.

Manually: unpack `mowdeck.zip` of a [release](https://github.com/lukislp/mowdeck-hacs/releases) into `<config>/custom_components/mowdeck/` (or copy `custom_components/mowdeck` of the tag) and restart Home Assistant.

## Create an API token in MowDeck

In the MowDeck web app: **Settings, API tokens, New token**. Give it a name ("Home Assistant"), the level (the whole account, or a site, group or single mower) and the permissions:

| Permission | Needed for |
| --- | --- |
| `mower.view` | required: mowers, states, map, live updates, the cameras, the health totals, **Check firmware now**. **Grant it on the whole account** to receive MowDeck notifications: only an account-wide `mower.view` implies `notifications.view`, which MowDeck needs to push the account's notifications to a token |
| `mower.control` | start, pause, dock, end job, blade height, mowing speed, light (without it Home Assistant is read only; the emergency stop works for viewers too) |
| `mower.drive` | optional: the manual drive speed setting (the number is disabled by default), **Start AI analysis** (Vision) and `mowdeck.spot_mow` (together with `mower.control`, which the Planner needs); also needed on a mower for its Vision notifications (`vision_proposal`, `vision_auto_proposal`) |
| `fleet.manage` | optional: installing a firmware update from the update entity, **Blade changed** |
| `schedule.edit` | optional: the schedule switches and **Skip next run** buttons (together with `mower.control`, which **Run now** needs alone). Seeing schedules, the next mowing, the last run and the rain decision only needs `mower.view` (`scheduler.view` follows it) |

The integration asks MowDeck which of these the token holds on each mower (`POST /api/v1/identity/me/permissions`, every 10 minutes) and only creates entities whose action the token may run; a MowDeck that cannot answer that question hides nothing, and a refused action names the missing permission.

**After updating MowDeck, issue the token again**: a token keeps the permissions it was created with, so a token from before MowDeck 0.7.0 holds no `notifications.view` and receives no notifications even with an account-wide `mower.view`. Create a new token with the same grants and enter it with **Reconfigure** on the integration, then revoke the old one.

The secret (`mdk_...`) is shown once. Through the API: `POST /api/v1/identity/accounts/{account_id}/api-tokens` with `{"name": "Home Assistant", "grants": [{"level": {"type": "account", "id": "<account id>"}, "permissions": ["mower.view", "mower.control"]}]}`.

## Configuration

**Settings, Devices and services, Add integration, MowDeck**:

| Field | Value |
| --- | --- |
| URL | the base URL of the MowDeck web app, for example `https://mowdeck.example.ts.net` or `http://192.168.1.20` |
| API token | the token from above |

The flow exchanges the token, reads `/api/v1/identity/me` and lists the mowers; the entry is keyed by the MowDeck account id, so the same account cannot be added twice.

**Options** (Configure on the entry): the poll interval while the live connection is down (default 60 s, 15 to 3600), whether to show the emergency stop button (default on), and **persistent notifications**: show MowDeck notifications of at least this severity also as persistent notifications in Home Assistant (off, critical only, warnings and critical, all; default off).

**Reconfigure** (three dots on the entry): a new URL when MowDeck moved, optionally with a new API token. Leave the token empty to keep the current one; a token of another MowDeck account is refused.

## Data updates

| Data | How | How often |
| --- | --- | --- |
| Mower states | pushed over the MowDeck Realtime WebSocket | live; polled every 60 s (the poll interval option) only while the socket is down |
| Mower list | polled | every 5 minutes while the socket is up (finds new and removed mowers) |
| Capabilities, map, token permissions | polled | every 10 minutes |
| Health totals (blade hours, mowing hours, distance, battery cycles) | polled, own coordinator | every 15 minutes |
| Schedules, their runs, the rain decision | polled, own coordinator | every 5 minutes and right after a schedule action |
| Notifications | pushed over the WebSocket (`notification` messages) | live; nothing is polled (see [Notifications](#notifications)) |
| Command results | pushed over the WebSocket | live |
| Vision proposals | the newest per mower, polled, own coordinator; changes pushed over the WebSocket (`vision_proposal`) | every 10 minutes; live while the socket is up |

Each MowDeck service has its own coordinator: when one fails, only its entities turn unavailable. When one answers "forbidden" (403), a Repairs issue names the permission the token lacks and links to MowDeck's API token page (`/settings/api-tokens`); it disappears with the next successful update.

## Repairs

| Issue | When | Goes away |
| --- | --- | --- |
| MowDeck refuses the health data / the schedules (`<service>_forbidden`) | a polled service answers 403 | with the next successful update |
| MowDeck live updates are refused (`realtime_unauthorized`) | the Realtime WebSocket refuses even a fresh access token | when the socket works again |
| The MowDeck API token receives no notifications (`missing_permission_notifications_view`) | `GET /identity/me` lists no `notifications.view` for the token (asked at setup and every 10 minutes): the token lacks an account-wide `mower.view`, or it was created before MowDeck 0.7.0 | once the token in use holds `notifications.view` |
| The Bluetooth bridge of *mower* needs a firmware update (`ble_bridge_outdated`) | the mower's state reports `links.ble_bridge.outdated` (with `outdated_reason`, the ESPHome and the bridge template version) | with the first state that reports current firmware |

The token issues link to MowDeck's API token page, the bridge issue to the mower's page. All issues of an entry disappear when it is unloaded.

## Entities

Per mower one device (manufacturer Mammotion, model from the capabilities, firmware as software version, a link to the mower page of the web app). The state sensors read only the typed fields of MowDeck's `MowerState`, never the adapter extras in `raw`; a field the mower's connection does not report stays unknown.

| Entity | Example id | Source |
| --- | --- | --- |
| Lawn mower | `lawn_mower.luba` | activity: `mowing`, `paused`, `returning`, `docked` (also while charging), `idle`, `error`; start, pause, dock (pauses the job), stop (ends the job and docks) |
| Battery | `sensor.luba_battery` | `battery_percent` |
| Activity | `sensor.luba_activity` | the MowDeck activity (enum, includes `charging`, `offline` and `updating`) |
| RTK fix, RTK source, Connection | `sensor.luba_rtk_fix`, ... | enums (diagnostic; RTK source and Connection are disabled by default) |
| Blade height, Speed | `sensor.luba_blade_height`, `sensor.luba_speed` | whole mm; m/s with two decimals |
| Last seen | `sensor.luba_last_seen` | `observed_at`, when the mower last reported values (diagnostic, disabled by default) |
| Error code | `sensor.luba_error_code` | `error_code` with the attributes `error_message`, `error_category` and `drive_recoverable` (diagnostic) |
| Error category | `sensor.luba_error_category` | `error_category` of the state, only while the activity is `error`: `trapped`, `obstacle`, `outside`, `wheel_slip`, `lift_tilt`, `collision`, `battery`, `blade_motor`, `rain`, `positioning`, `other` (unknown otherwise) |
| Job progress, Current area, Job time elapsed, Job time remaining, Mowed area | `sensor.luba_job_progress`, `sensor.luba_current_area`, ... | the running job (`job` of the state): progress in %, the map area it works in (named as in MowDeck, attribute `area_id`), elapsed and remaining time (shown in minutes; Mammotion reports whole minutes), mowed m² of this job. Unknown while no job runs |
| RTK status | `sensor.luba_rtk_status` | the receiver's solution (`gnss.rtk_status`): `none`, `single` (no corrections), `float`, `fixed` (diagnostic; finer than RTK fix, which folds `single` into `none`) |
| Satellites, RTK correction age | `sensor.luba_satellites`, `sensor.luba_rtk_correction_age` | satellites in use and the age of the last RTK correction in s (diagnostic, disabled by default: they change with nearly every state) |
| Wi-Fi signal, Mobile signal, Network | `sensor.luba_wi_fi_signal`, `sensor.luba_mobile_signal`, `sensor.luba_network` | the mower's own radio (`signal`): Wi-Fi and LTE in dBm (diagnostic, disabled by default) and its uplink `wifi`, `mobile`, `ble`, `none` (diagnostic) |
| Self-check | `sensor.luba_self_check` | the mower's start self-check (`self_check`): `ok` or `blocked`, attribute `detail` with the reason (`rain`, `lifted`, `low_battery`, `outside_task_area`, `non_working_hours`, `rtk_not_ready`, `other_<n>`) |
| Blade hours since change, Mowing hours, Distance driven, Battery cycles | `sensor.luba_blade_hours_since_change`, ... | MowDeck's health totals (`/telemetry/mowers/{id}/health`, refreshed every 15 minutes); blade hours follow the counter MowDeck shows (the mower's own or MowDeck's) with the attributes `threshold` (the reminder threshold of that counter) and `changed_at` (the last blade change recorded in MowDeck), distance in km; battery cycles are the mower's own lifetime counter when it reports one (attribute `source`: `device`, MowDeck's own count in `tracked_cycles`), MowDeck's count otherwise (`source`: `telemetry`) |
| Bluetooth bridge signal | `sensor.luba_bluetooth_bridge_signal` | RSSI of the mower at the bridge in dBm (diagnostic, disabled by default); only for mowers whose connection supports a `ble_bridge` link |
| Charging, Error | `binary_sensor.luba_charging`, `binary_sensor.luba_error` | |
| Online | `binary_sensor.luba_online` | the gateway holds a live link: not stale, not `connected: false`, a state within the last 5 minutes (the gateway repeats it every 10 s), not `offline` |
| Emergency stop, Return to dock, End job | `button.luba_emergency_stop`, `button.luba_return_to_dock`, `button.luba_end_job` | the emergency stop can be hidden in the options; Return to dock pauses a running job (start resumes it), End job ends it (`return_to_dock` with `end_job`) |
| Check firmware now | `button.luba_check_firmware_now` | asks the vendor for a firmware update now (`POST /fleet/mowers/{id}/firmware/check`); the firmware entity shows the answer at once. Only for mowers whose connection can update firmware |
| Blade changed | `button.luba_blade_changed` | records a blade change in MowDeck (`POST /telemetry/mowers/{id}/health/blade-reset`): the blade hours since the change restart at zero. Disabled by default; needs `fleet.manage` |
| Blade height, Mowing speed | `number.luba_blade_height`, `number.luba_mowing_speed` | ranges from the mower's capabilities (`blade_height_range_mm`, `min_speed_mps` / `max_speed_mps`); the speed in steps of 0.01 m/s, shown and sent with two decimals |
| Manual drive speed | `number.luba_manual_drive_speed` | the vendor's speed setting for joystick driving (`drive_speed_setting`, `set_drive_speed`); disabled by default, needs `mower.drive`; only for mowers that have the setting |
| Firmware | `update.luba_firmware` | installed and offered firmware with release notes and progress (`firmware_info`); install sends `firmware_update` (MowDeck requires the mower docked with enough battery; needs `fleet.manage`); only for mowers whose connection can update firmware |
| Schedule *name* | `switch.luba_schedule_morning` | one per MowDeck schedule of the mower or of one of its groups: on = enabled (`PATCH /scheduler/schedules/{id}`). Attributes: days, start time, window, areas, next run, why MowDeck disabled it. Needs `schedule.edit` and `mower.control`. **MowDeck makes whoever changes a schedule its owner**, so after a toggle in Home Assistant the schedule fires on behalf of the API token (and stops when the token is revoked) |
| Run *name* now, Skip next run of *name* | `button.luba_run_morning_now`, `button.luba_skip_next_run_of_morning` | start the schedule now without its rain pause (`mower.control`); skip its next firing (`schedule.edit`) |
| Next mowing | `sensor.luba_next_mowing` | the earliest next firing of the mower's enabled schedules (a skipped firing is passed over), attribute `schedule` |
| Last schedule run | `sensor.luba_last_schedule_run` | outcome of the newest run of the mower: `started`, `skipped_rain`, `skipped_permission`, `skipped_window`, `skipped_mower_busy`, `skipped_manual`, `failed`; attributes `schedule`, `scheduled_for`, `trigger`, `details` |
| Rain hold | `binary_sensor.luba_rain_hold` | on while MowDeck's rain pause holds the mower's runs (`GET /scheduler/weather/{id}` with the rain settings of the mower's next schedule, MowDeck's defaults without one); attributes `verdict`, `reason`, `wet`, recent rain, provider |
| Rain pause until, Lawn wet until | `sensor.luba_rain_pause_until`, `sensor.luba_lawn_wet_until` | when the hold ends and when the lawn is estimated dry (unknown when not holding or not wet) |
| Area | `select.luba_area` | `all`, one map area, or `selection` (several areas chosen with the area switches); areas named as in MowDeck (the name given in MowDeck, else the mower's own name, else "Area N" / "Bereich N" in map order); restored after a restart |
| Mow *area* | `switch.luba_mow_front`, ... | one per map area: whether "start mowing" mows it. The switches are the multi-area selection, the Area select its shortcut; both always agree |
| Mowing pattern, Mowing angle, Border laps, Path spacing | `select.luba_mowing_pattern`, `number.luba_mowing_angle`, `number.luba_border_laps`, `number.luba_path_spacing` | the mower's mowing defaults in MowDeck (`MowerOut.mow_defaults`, `PUT /fleet/mowers/{id}/mow-defaults`), shared with the web app and schedules: the pattern among the mower's `native_patterns`, angle 0 to 180°, border laps 0 to 4, path spacing in cm within `path_spacing_range_mm` (only where the model has one; unknown while the mower's own spacing applies). Need `mower.control` |
| Mowing time today, Distance today, Jobs today, Online time today | `sensor.luba_mowing_time_today`, ... | totals of the current **UTC** day (`/telemetry/mowers/{id}/health/daily`, MowDeck counts days in UTC), every 15 minutes; online time is diagnostic and disabled by default |
| Position | `device_tracker.luba_position` | GPS (`position` of the state), attributes `heading` (degrees, one decimal) and `position_source` (`rtk`, `gnss` or `dock`) |
| Camera | `camera.luba_camera` | the live camera over MowDeck's camera relay as WebRTC (see [Cameras](#cameras)); only for mowers whose capabilities name a camera (`camera` `cloud` or `local`) |
| Further cameras | `camera.luba_front_right_camera`, ... | one per further camera that delivers video, added after the first stream (see [Cameras](#cameras)) |
| Start AI analysis | `button.luba_start_ai_analysis` | starts a Vision analysis (see [Vision](#vision-ai-assist)); needs `mower.drive` |
| AI recovery | `sensor.luba_ai_recovery` | state of the mower's newest Vision proposal and what it proposes (see [Vision](#vision-ai-assist)) |
| AI recovery image, AI recovery map | `image.luba_ai_recovery_image`, `image.luba_ai_recovery_map` | the camera frame the model saw and Vision's top-down map rendering (the map disabled by default) |
| AI recovery (event) | `event.luba_ai_recovery` | fires `created` and `updated` for every proposal change |
| Spot run | `sensor.luba_spot_run` | the spot run MowDeck drives (`spot_run` of the state): `preparing`, `undocking`, `waiting_rtk`, `transit`, `spot_mowing`, `paused_rtk`, `returning`, `stopping`, `done`, `aborted`, `failed` (kept until the next run); attributes `progress_percent`, `driven_points`, `planned_points`, `segment_index`, `segments_total`, `dry_run`, `reason`, `run_id`. Only for mowers MowDeck can drive (`manual_drive`) |
| Stop spot run | `button.luba_stop_spot_run` | `spot_stop`: zero frame, blades off, the mower stays where it is. Only for mowers MowDeck can drive; needs `mower.control` |
| Camera light | `switch.luba_camera_light` | the camera's headlamp: `light_on` of the state (unknown while not reported), `set_light`; only for mowers whose capabilities have `light` |

**Start mowing** resumes a paused job; otherwise it starts the selected areas (area switches / Area select) in map order, with the mower's mowing defaults as pattern once they are saved in MowDeck (until then the mower's own defaults). With every area switch off, starting is refused.

The event `mowdeck_command_result` carries every command status change (`mower_id`, `command_id`, `status`, `reason`); the entity `event.luba_command` fires with the status as event type (`pending`, `accepted`, `rejected`, `executed`, `expired`, `failed`) and `command_id` and `reason` as attributes.

## Notifications

MowDeck notifications (mower stuck, battery low, mowing finished, theft alarms, firmware updates and so on) reach Home Assistant three ways:

- the entity `event.luba_notification` per mower: the event type is the notification kind (`mowing_finished`, `mower_stuck`, `battery_low`, `mower_offline`, `mower_online`, `job_started`, `mower_lifted`, `mower_left_dock_unexpectedly`, `geofence_exit`, `blade_maintenance`, `vision_proposal`, `vision_auto_proposal`, `firmware_update_available`, `firmware_update_finished`, `firmware_update_failed`, `test`), the attributes are `notification_id`, `scope`, `severity` (`info`, `warning`, `critical`), `title`, `body`, `url` and `occurred_at`;
- the bus event `mowdeck_notification` with the same fields plus `mower_id`, `mower_name` and `config_entry_id` (also for notifications without a mower and for kinds this version does not know);
- optionally a persistent notification (see the options).

Both events have logbook entries.

**How they arrive.** MowDeck (0.7.0 or newer) pushes every notification of a subscribed mower over the Realtime WebSocket as a `notification` message with `scope` `account` to the account's API tokens that hold `notifications.view`. The integration subscribes to every mower it sees, so nothing is polled and nothing else needs configuring. Requirements and limits:

- The token needs `mower.view` **on the whole account** (that implies `notifications.view`); a token limited to a site, group or mower receives none. Vision kinds also need `mower.drive` on the mower. A token created before MowDeck 0.7.0 must be issued again (see [Create an API token](#create-an-api-token-in-mowdeck)).
- Account notifications are rendered in English, whatever language the people of the account use.
- They have no inbox item behind them: Home Assistant cannot mark them read or acknowledge them (MowDeck answers an API token's `GET /notifications/inbox` with 403, so the inbox is never asked).
- MowDeck replays nothing after a reconnect: a notification raised while Home Assistant was offline or the socket was down is not delivered later.
- Every notification fires once: Home Assistant remembers the delivered ids, also across restarts.


## Vision (AI assist)

MowDeck's Vision service looks at a photo of a stuck mower and proposes how to free it: a few short drive steps with the blades off, returning to the dock, or resuming the paused job. Home Assistant can **start** an analysis and **show** the proposal; approving or rejecting it stays in MowDeck, where a person sees the picture and the steps before the mower moves (the `link` attribute opens the proposal there).

| Entity | What it does |
| --- | --- |
| `button.luba_start_ai_analysis` | `POST /api/v1/vision/mowers/{id}/recover` with task `recover` and no images: the mower's gateway takes the photo. Only offered when the token holds `mower.drive` on the mower. MowDeck refuses while a proposal of the mower is still open (409), without a usable AI provider (409 `vision-provider-missing`: an API token has no provider of its own, so the installation's hosted default must be enabled and the plan must include `ai_recover_hosted`), beyond the daily cap (429) and without the plan feature (403 `feature-not-in-plan`); the error names MowDeck's reason |
| `sensor.luba_ai_recovery` | the state of the newest proposal: `analysing`, `proposed` (waiting for approval in MowDeck), `rejected_by_ai` (the model proposes nothing), `failed`, `rejected`, `expired`, `executing`, `verifying`, `recovered`, `not_recovered`, `aborted`. Attributes: `proposal_id`, `task`, `trigger` (`manual` or `error`), `round`, `action` (`drive`, `return_to_dock`, `resume`), `fallback`, `step_count`, `drive_seconds`, `steps` (each with `linear`, `angular`, `seconds`, `outcome`), `confidence`, `situation`, `rationale`, `detail`, `final`, `expires_at` (approval is refused after it), `link` |
| `image.luba_ai_recovery_image` | the camera frame the model saw (the left camera of a stereo pair); fetched once per proposal from `GET /vision/proposals/{id}/images/{index}`. MowDeck purges the images after the proposal's TTL; then the image is empty |
| `image.luba_ai_recovery_map` | the top-down map rendering around the mower that Vision adds to the photo (disabled by default) |
| `event.luba_ai_recovery` | event types `created` and `updated`, attributes `proposal_id`, `state`, `action`, `final`, `link`; also the bus event `mowdeck_vision_proposal` (with `mower_id` and `config_entry_id`, logbook entry) |

The entities exist only when MowDeck runs the Vision service. The newest proposal is read at startup and every 10 minutes; every change arrives at once over the Realtime WebSocket (`vision_proposal`), after which the full proposal (frames, assessment, steps) is read. A MowDeck notification of kind `vision_proposal` or `vision_auto_proposal` also arrives when the token holds `mower.drive` (see [Notifications](#notifications)).

```yaml
# Show the proposal on the phone, with a button to approve it in MowDeck.
triggers:
  - trigger: state
    entity_id: sensor.luba_ai_recovery
    to: proposed
actions:
  - action: notify.mobile_app_phone
    data:
      title: "Luba: proposal waiting"
      message: "{{ state_attr('sensor.luba_ai_recovery', 'situation') }}"
      data:
        image: /api/image_proxy/image.luba_ai_recovery_image
        url: "{{ state_attr('sensor.luba_ai_recovery', 'link') }}"
```

## Cameras

The cameras play the same way as in the MowDeck web app: through MowDeck's camera relay ([ADR 0045](https://github.com/lukislp/mowdeck/blob/main/docs/adr/0045-camera-relay.md)), as H.264 over WebRTC. Home Assistant only does the signalling; the video goes straight from MowDeck's media server to the browser or the app.

**Requirements**

- MowDeck 0.1.0 or newer with the camera relay running (Compose profile `relay`, Helm `services.cameraRelay.enabled`) and, for Mammotion mowers, the operator-built Agora bridge (Compose profile `agora`). Without a relay source for the mower the camera reports "MowDeck's camera relay cannot serve this mower".
- The plan feature `camera` on the MowDeck account and `mower.view` on the mower for the API token. Nothing else: the integration uses the same API and token as everything else.
- The relay's media port (`RELAY_ICE_PORT`, default 8189, UDP and TCP) reachable **from the device that shows the dashboard**, under one of the addresses in `RELAY_ICE_HOSTS`. In the home network that is the MowDeck host's LAN address, exactly as for the web app. Away from home the device needs the same path to that port as the MowDeck web app does away from home (for example Tailscale with the tailnet address in `RELAY_ICE_HOSTS`); Home Assistant's own remote access (Nabu Casa) does not carry it, because the media never passes Home Assistant.

**How a stream runs**

1. The frontend opens the camera and sends its WebRTC offer to Home Assistant.
2. The integration opens a viewer lease of the API token (`POST /api/v1/fleet/mowers/{id}/camera/session` with `transport` `relay`) and posts the offer with the lease's ticket to the camera's WHEP URL (`/media/whep/...`). While the mower's camera starts the media server answers 404; the offer goes out again (0.5 s growing to 3 s) for up to 40 s. The answer goes back to the browser; the browser's ICE candidates follow to the WHEP session (trickle ICE, `PATCH`).
3. While the stream is open the lease is renewed every `heartbeat_seconds` (15 s); every renewal brings a fresh ticket.
4. When the frontend closes the stream (the dialog or the dashboard is closed, the browser goes away) the integration deletes the WHEP session and the lease at once; the relay stops the mower's stream after its grace of 15 s. A lease is opened only for an offer, so nothing streams without a viewer in Home Assistant, and every open stream holds its own lease.
5. A lease that is gone on the MowDeck side (a renewal answered 404) ends the stream with "The camera stream ended on the MowDeck side"; opening the camera again starts a new lease.

The leases belong to the API token. As in MowDeck, only the principal that opened a lease can renew or end it, and another account never sees it.

**Which cameras**

Which cameras a mower has is only known from a camera session, and opening one starts the mower's stream, so the integration never opens one by itself. Every camera-capable mower gets `Camera` at once: it plays the camera the web app shows first (the first one known to deliver; a front camera on a LUBA 2 AWD X). Its first stream lists the cameras: every other camera that delivers video gets its own entity (`Front right camera` and so on), and the list is kept in Home Assistant's storage so these entities exist after a restart. A camera the relay reports as silent (listed by the vendor but never published, such as the LUBA 2 AWD X's rear camera) gets no entity, and an entity whose camera turned silent is unavailable until a later stream finds it delivering. The attributes `camera_id` and `placement` say which camera an entity plays.

**No still image**: MowDeck offers no snapshot to API clients (the relay's frame tap serves only MowDeck's own Vision service), so the camera entities have no picture of their own. Picture cards stay empty; the stream in the more-info dialog and in a picture-glance or camera card with `camera_view: live` works.

## Actions

Targets are MowDeck lawn mower entities.

| Action | Fields | API command |
| --- | --- | --- |
| `mowdeck.start_mow` | `area_ids` (list, optional; default: the selection), `pattern` (object, optional) | `start_mow` |
| `mowdeck.emergency_stop` | | `emergency_stop` |
| `mowdeck.set_blade_height` | `mm` (10 to 120) | `set_blade_height` |
| `mowdeck.set_speed` | `mps` (up to 2, sent with two decimals) | `set_speed` |
| `mowdeck.spot_mow` | `latitude` and `longitude`, or `zone`; `radius` (0.5 to 3 m), `pattern` (`spiral`, `stripes`), `angle`, `speed` (0.1 to 0.3 m/s), `blades`, `confirm_blades`, `return_to_dock`, `turn_style` | plans the spot (`POST /planner/plans`), then `spot_mow`; optional response (see [Spot mowing](#spot-mowing)) |
| `mowdeck.get_map` | | answers the current map (`GET /telemetry/mowers/{id}/map`) with `display_name` per area; response only |
| `mowdeck.get_heatmap` | `layer` (`coverage`, `rtk`, `wifi`, `mnet`), `start`, `end` (default the last 7 days), `cell` (0.5, 1 or 2 m) | answers the heatmap layer (`GET /telemetry/mowers/{id}/heatmap`); response only |

```yaml
action: mowdeck.start_mow
target:
  entity_id: lawn_mower.luba
data:
  area_ids: ["zone-front", "zone-back"]
  pattern: {name: stripes, angle_deg: 45}
```

```yaml
action: mowdeck.get_heatmap
target:
  entity_id: lawn_mower.luba
data:
  layer: coverage
response_variable: heatmap
```

## Spot mowing

`mowdeck.spot_mow` mows a small circle the way the spot sheet of the MowDeck web app does ([ADR 0039](https://github.com/lukislp/mowdeck/blob/main/docs/adr/0039-spot-mowing-by-mowdeck-driven-paths.md)): MowDeck's Planner computes the route, then MowDeck's gateway drives the mower along it with joystick frames. The mower runs no job of its own, so this works on mowers that cannot follow custom paths (the LUBA), as long as MowDeck can drive them (`manual_drive`).

- **Where:** `latitude` and `longitude`, or a `zone` (its centre; its radius unless `radius` is given). `radius` is 0.5 to 3 m (default 1.5 m), the web app's limits. The whole circle must lie inside one mapped area; the Planner keeps 0.4 m from its edge and from no-go zones.
- **How:** `pattern` `spiral` (inward, default) or `stripes` at `angle`; `speed` 0.1 to 0.3 m/s (MowDeck's hard cap is 0.3); `turn_style` `in_place`, `arc` or `multi_point` (three-point turns need a drive link that reverses, else arcs; default: the mower's own setting); path spacing is the cutting width minus 5 cm.
- **Blades:** off unless `blades` **and** `confirm_blades` are both true and the mower allows blades while MowDeck drives it (`manual_drive_blades`). `blades` without `confirm_blades` is refused, never silently turned into a dry run.
- **When:** the mower must be idle with a fixed RTK position (or docked, when its connection can leave the dock by itself: the run undocks, then waits for RTK), with no job or spot run active and the battery above 20 %. The Planner must find a way to the spot. Home Assistant checks these first and names the one that fails; MowDeck's gateway checks them again (link, RTK and dead-man while driving).
- **After:** with `return_to_dock` (default) the mower goes home after the run, also after an abort that left it healthy, never after a stop. `sensor.luba_spot_run` shows the progress, `button.luba_stop_spot_run` ends the run.
- **Response** (optional): `command_id`, `area_id`, `blades`, `turn_style`, `estimated_duration_s`, `covered_area_m2`, `plan_hash`.
- **Needs** `mower.control` (Planner) and `mower.drive` (`spot_mow`) on the mower and the plan feature `spot_mowing`.

```yaml
action: mowdeck.spot_mow
target:
  entity_id: lawn_mower.luba
data:
  zone: zone.flower_bed
  radius: 1.5
  pattern: spiral
  speed: 0.2
```

## Troubleshooting

- **"MowDeck live updates are refused" (Repairs)**: the Realtime WebSocket closes with 4401 even with a fresh access token. Check that the token still exists and has `mower.view`. Polling continues meanwhile; the issue disappears when the socket works again.
- **No notifications arrive**: the token lacks `notifications.view`. Grant `mower.view` on the whole account (not a site, group or mower) and, after updating MowDeck to 0.7.0 or newer, issue a new token (old tokens keep their old permission set). Vision notifications also need `mower.drive` on the mower. Notifications raised while the live connection was down are not delivered later.
- **Reauthentication requested**: the API token was revoked, expired or its owner disabled. Create a new token and enter it in the reauth dialog (it must belong to the same account).
- **Cannot connect**: use the URL that Home Assistant itself can reach (the Docker network, the LAN address, or the Tailscale name). Behind Tailscale, the Home Assistant host must be in the tailnet; MagicDNS names such as `https://mowdeck.<tailnet>.ts.net` work once HTTPS certificates are enabled for the tailnet.
- **HTTPS**: with `https://` the WebSocket uses `wss://` on the same host. Self-signed certificates are not accepted; use Tailscale HTTPS or a certificate from a public CA.
- **Commands fail with "may not send"**: the token lacks the permission the message names on that mower (`mower.control` for the job commands, `mower.drive` for the drive speed, `fleet.manage` for firmware updates).
- **Camera stays black or "connecting"**: the browser does not reach the relay's media port. Check from the same device that `RELAY_ICE_HOSTS` contains an address it can reach and that port 8189 (UDP and TCP) is open on the MowDeck host; the MowDeck web app on that device shows the same problem then.
- **"The mower's camera did not start in time"**: the mower did not publish within 40 s (4G gaps, a sleeping mower that wakes slowly). Open the camera again.
- **"MowDeck refuses the mower health data" (Repairs)**: the health totals answer 403 for the token. Grant the named permission; the issue disappears with the next update (every 15 minutes).
- **"MowDeck refuses the schedules" (Repairs)**: the Scheduler answers 403; grant `mower.view` (or `scheduler.view`) to the token. A MowDeck without the Scheduler simply shows no schedule or rain entities.
- **No schedule switch or skip button**: the token lacks `schedule.edit` (or `mower.control`) on the schedule's mower or group; MowDeck is asked every 5 minutes, so new grants show up without a restart.
- **Diagnostics** (three dots on the entry) contain mowers, states, capabilities, the token's permissions per mower, the health totals and the realtime status; the API token, connection options, serials, vendor ids, positions and all coordinates are redacted, and maps are reduced to counts (no area names or boundaries).

## Removal

1. **Settings, Devices and services, MowDeck**, three dots on the entry, **Delete**. This removes the devices and entities of that MowDeck account from Home Assistant.
2. In the MowDeck web app (**Settings, API tokens**) revoke the token you created for Home Assistant.
3. Optionally uninstall **MowDeck** in HACS (or delete `<config>/custom_components/mowdeck`) and restart Home Assistant.

A single mower that is no longer visible to the token disappears from Home Assistant by itself after the next poll; its device can also be deleted by hand.

## Development

This repository is the home of the integration; it was moved here from the MowDeck monorepo (`integrations/home-assistant`, see `CHANGELOG.md`). The integration is a pure client of the public MowDeck API; the OpenAPI document of [lukislp/mowdeck](https://github.com/lukislp/mowdeck) is its contract. Home Assistant's test harness needs Linux or WSL (it imports `fcntl`); on Windows only lint and mypy run.

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov
uv run python scripts/check_hacs_layout.py
```

CI (`.github/workflows/ci.yml`) runs the same plus hassfest and the HACS validation; releases, Dependabot and the security checks are described in `CONTRIBUTING.md` and `SECURITY.md`.

## Licence

AGPL-3.0-only, see `LICENSE`.
