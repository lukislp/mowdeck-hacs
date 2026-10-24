# MowDeck for Home Assistant

[![CI](https://github.com/lukislp/mowdeck-hacs/actions/workflows/ci.yml/badge.svg)](https://github.com/lukislp/mowdeck-hacs/actions/workflows/ci.yml) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lukislp/mowdeck-hacs/badge)](https://scorecard.dev/viewer/?uri=github.com/lukislp/mowdeck-hacs) [![CodeQL](https://github.com/lukislp/mowdeck-hacs/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/lukislp/mowdeck-hacs/security/code-scanning)
[![Release](https://img.shields.io/github/v/release/lukislp/mowdeck-hacs)](https://github.com/lukislp/mowdeck-hacs/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz/docs/faq/custom_repositories/)
[![License: AGPL-3.0](https://img.shields.io/github/license/lukislp/mowdeck-hacs)](LICENSE)

Custom integration (HACS) that brings the mowers of a [MowDeck](https://github.com/lukislp/mowdeck) installation into Home Assistant. It is a pure client of the public MowDeck API: it authenticates with an API token, reads mowers, states, capabilities and maps over HTTP, receives live states over the MowDeck Realtime WebSocket and sends commands through Fleet. It needs no Python packages beyond Home Assistant itself.

- Domain `mowdeck`, IoT class `local_push`, integration type `hub` (one entry per MowDeck account, one device per mower).
- Requires Home Assistant 2026.10.0 or newer (`hacs.json`).
- [Integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/): Gold (`custom_components/mowdeck/quality_scale.yaml`; tests keep the coverage above 95 %).
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
| `fleet.manage` | optional: installing a firmware update from the update entity, **Blade changed**, the **Blade change reminder** number, the [mower's own settings](#mower-settings) |
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

## Supported devices

The integration talks to MowDeck, not to the mowers: it supports every mower a MowDeck installation runs, with the entities that mower's capabilities allow (patterns, cameras, light, manual driving, reverse, firmware updates, settings, maintenance commands and so on are created only where the mower's connection reports them).

| Device | Through MowDeck | Status |
| --- | --- | --- |
| Mammotion LUBA 2 family (LUBA 2 AWD, LUBA 2 AWD X, also with 4G and RTK) | MowDeck's Mammotion adapter: the Mammotion cloud, optionally a Bluetooth bridge (an ESPHome Bluetooth proxy) for manual driving, spot mowing and the nudges | in daily use with a LUBA 2 AWD X 4G RTK |
| Mammotion LUBA mini, YUKA | the same adapter | supported by MowDeck; the entities follow what the adapter reports, not verified with Home Assistant |
| Mammotion RTK base station (LUBA 2 family) | as its own device, through a linked mower (MowDeck 0.11) | see [RTK base stations](#rtk-base-stations) |
| MowDeck simulator | the simulator connection | for trying MowDeck and Home Assistant without hardware |

What the integration needs from MowDeck:

- A reachable MowDeck installation (self-hosted, see [lukislp/mowdeck](https://github.com/lukislp/mowdeck)) and an API token of its account. Mowers MowDeck has no adapter for cannot be added.
- Features follow the MowDeck version: notifications need 0.7, the camera still image and the blade change reminder 0.8, the mower settings, maintenance commands and RTK base stations 0.11. With an older MowDeck those entities are simply missing; the rest works.

## Use cases

- **Keep the lawn dry**: send the mower home when its own rain sensor reports rain or MowDeck's rain pause holds, and leave the restart to the schedules (MowDeck skips runs while the lawn is wet).
- **Know when something goes wrong**: get a phone notification when the mower is stuck, lifted, leaves its dock unexpectedly or leaves the geofence, with a link to the mower in MowDeck.
- **Free a stuck mower from the sofa**: start an AI analysis, see the photo and the proposal on the phone, approve it in MowDeck with one tap; or nudge the mower a little with the emergency nudges.
- **Mow around people**: pause or skip a run when the family is in the garden (a person, a door sensor or a calendar), resume afterwards.
- **See the garden**: the map with the planned path, coverage and signal heatmaps and the missed area on a dashboard; the mowing calendar next to the family calendar.
- **Look after the mower**: blade hours with a reminder, firmware updates for the mower and the RTK base station, the base station's link and whether it was moved.

## Data updates

| Data | How | How often |
| --- | --- | --- |
| Mower states | pushed over the MowDeck Realtime WebSocket | live; polled every 60 s (the poll interval option) only while the socket is down |
| Mower list | polled | every 5 minutes while the socket is up (finds new and removed mowers) |
| Capabilities, map, token permissions | polled | every 10 minutes |
| Health totals (blade hours, mowing hours, distance, battery cycles) | polled, own coordinator | every 15 minutes |
| Schedules, their runs, the next 10 firings of each enabled schedule, the rain decision | polled, own coordinator | every 5 minutes and right after a schedule action |
| Notifications | pushed over the WebSocket (`notification` messages) | live; nothing is polled (see [Notifications](#notifications)) |
| Command results | pushed over the WebSocket | live |
| Vision proposals | the newest per mower, polled, own coordinator; changes pushed over the WebSocket (`vision_proposal`) | every 10 minutes; live while the socket is up |
| Heatmap layers (missed area, heatmap images) | polled, own coordinator: the coverage layer of every mower, the other layers only while their image is enabled | every 30 minutes (a layer is a query over a week of positions) |
| Planned path of the running job (map image) | polled while a job runs | at most every 30 s |

Each MowDeck service has its own coordinator: when one fails, only its entities turn unavailable. When one answers "forbidden" (403), a Repairs issue names the permission the token lacks and links to MowDeck's API token page (`/settings/api-tokens`); it disappears with the next successful update.

## Repairs

| Issue | When | Goes away |
| --- | --- | --- |
| MowDeck refuses the health data / the schedules / the heatmaps (`<service>_forbidden`) | a polled service answers 403 | with the next successful update |
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
| Blade change reminder | `number.luba_blade_change_reminder` | MowDeck's reminder threshold in blade hours since the change (MowDeck 0.8, `PUT /telemetry/mowers/{id}/health/blade-threshold`): the mower's own threshold, else MowDeck's default; attribute `custom`. It concerns MowDeck's own count and notification; the mower's own warning stays as set in the vendor app. Needs `fleet.manage`; only with a MowDeck that offers it |
| Blade changed | `button.luba_blade_changed` | records a blade change in MowDeck (`POST /telemetry/mowers/{id}/health/blade-reset`): the blade hours since the change restart at zero. Disabled by default; needs `fleet.manage` |
| Blade height, Mowing speed | `number.luba_blade_height`, `number.luba_mowing_speed` | ranges from the mower's capabilities (`blade_height_range_mm`, `min_speed_mps` / `max_speed_mps`); the speed in steps of 0.01 m/s, shown and sent with two decimals |
| Manual drive speed | `number.luba_manual_drive_speed` | the vendor's speed setting for joystick driving (`drive_speed_setting`, `set_drive_speed`); disabled by default, needs `mower.drive`; only for mowers that have the setting |
| Firmware | `update.luba_firmware` | installed and offered firmware with release notes and progress (`firmware_info`); install sends `firmware_update` (MowDeck requires the mower docked with enough battery; needs `fleet.manage`); only for mowers whose connection can update firmware |
| Schedule *name* | `switch.luba_schedule_morning` | one per MowDeck schedule of the mower or of one of its groups: on = enabled (`PATCH /scheduler/schedules/{id}`). Attributes: days, start time, window, areas, next run, why MowDeck disabled it. Needs `schedule.edit` and `mower.control`. **MowDeck makes whoever changes a schedule its owner**, so after a toggle in Home Assistant the schedule fires on behalf of the API token (and stops when the token is revoked) |
| Run *name* now, Skip next run of *name* | `button.luba_run_morning_now`, `button.luba_skip_next_run_of_morning` | start the schedule now without its rain pause (`mower.control`); skip its next firing (`schedule.edit`) |
| Mowing schedule | `calendar.luba_mowing_schedule` | the next mowing windows of the mower's enabled schedules (its own and its groups'): the next 10 firings each schedule previews (`GET /scheduler/schedules/{id}/preview`), from the start to the end of the window, named after the schedule, the description naming its areas (none: every area). Firings skipped with **Skip next run** are left out; the state is on while a window runs. Read only |
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
| Leave the dock | `button.luba_leave_the_dock` | `leave_dock`: the mower drives out of its charging station and stops in front of it. Needs `mower.control`; only where the connection offers it (see [Maintenance and emergency nudges](#maintenance-and-emergency-nudges)) |
| Restart mower, Read map again, Relocate charging station, Read RTK and dock position again | `button.luba_restart_mower`, `button.luba_read_map_again`, `button.luba_relocate_charging_station`, `button.luba_read_rtk_and_dock_position_again` | maintenance commands; configuration, disabled by default, need `fleet.manage` (see [Maintenance and emergency nudges](#maintenance-and-emergency-nudges)) |
| Emergency nudge forward, back, left, right | `button.luba_emergency_nudge_forward`, ... | one short `manual_drive` frame; configuration, disabled by default, need `mower.drive`; only for mowers MowDeck can drive, backwards only where they may reverse |
| Map | `image.luba_map` | the mower's map rendered as SVG in Home Assistant (see [Map and heatmaps](#map-and-heatmaps)); only for mowers with a map |
| Coverage heatmap, RTK heatmap, Wi-Fi heatmap, Mobile network heatmap | `image.luba_coverage_heatmap`, `image.luba_rtk_heatmap`, `image.luba_wi_fi_heatmap`, `image.luba_mobile_network_heatmap` | a heatmap layer of the last 7 days as SVG; disabled by default (only an enabled one is read from MowDeck); attributes `from`, `to`, `cell_m`, `legend` (`ratio`, `fix`, `dbm`), `legend_min`, `legend_max`, `samples`, `truncated` |
| Missed area | `sensor.luba_missed_area` | the area the blades missed in the last 7 days: the sum of the coverage layer's missed spots (`gaps[].area_m2`) in m², attributes `spots`, `from`, `to`; refreshed every 30 minutes |
| Camera light | `switch.luba_camera_light` | the camera's headlamp: `light_on` of the state (unknown while not reported), `set_light`; only for mowers whose capabilities have `light` |
| Mower settings | `switch.luba_rain_detection`, `number.luba_voice_volume`, `select.luba_turning_style`, `time.luba_non_working_hours_start`, ... | the mower's own settings as its connection declares them (see [Mower settings](#mower-settings)); configuration entities, need `fleet.manage` |
| Rain | `binary_sensor.luba_rain` | the mower's own rain sensor (`rain_detected`, device class moisture): on while it reports rain. **Only reported while the mower's rain detection is on**; otherwise (and with connections that do not report the sensor) unavailable |

**Start mowing** resumes a paused job; otherwise it starts the selected areas (area switches / Area select) in map order, with the mower's mowing defaults as pattern once they are saved in MowDeck (until then the mower's own defaults). With every area switch off, starting is refused.

The event `mowdeck_command_result` carries every command status change (`mower_id`, `command_id`, `status`, `reason`); the entity `event.luba_command` fires with the status as event type (`pending`, `accepted`, `rejected`, `executed`, `expired`, `failed`) and `command_id` and `reason` as attributes.

## Mower settings

MowDeck reads and changes some of the mower's own settings (the ones the vendor app keeps on the mower), as far as the mower's connection supports them (MowDeck 0.11). The connection declares each setting with its type and range or options (`MowerCapabilities.settings`), the mower reports the values (`MowerState.settings`), and the integration creates one configuration entity per declared setting:

| Type | Entities | Sends |
| --- | --- | --- |
| on/off | a switch | `set_setting` with `true` / `false` |
| number | a number with the declared range, step and unit | a whole number |
| option | a select with the declared options | the option |
| daily time window | two time entities (start, end) and a button **Turn off ...** | `{"start": "HH:MM", "end": "HH:MM"}` in the mower's local time (an end before the start crosses midnight); the button sends `null` |

For a LUBA 2 (AWD) these are: **Rain detection**, **Turning style** (turn in place, multi-point turn), **Route between areas** (direct, along the perimeter), **Side lights**, **Night light** (models with a fill light), **Wildlife protection** (off, stop mowing, mow slowly; firmware that supports it), **Voice prompts**, **Voice volume** (0 to 100 %), **Voice** (male, female), **Non-working hours**, and from firmware 2.1.1.5 on **Return to charge at** (15 to 30 %), **Smart return level**, **Resume mowing at** (40 to 100 %) and **Smart resume level**. A setting MowDeck knows no name for shows as "Setting *key*".

- The entities follow the declaration: a setting the mower no longer declares (other firmware, other connection) disappears with its entities. A value the mower has not reported yet is unknown; after a change the new value shows once the mower reports it.
- Changing a setting needs `fleet.manage` on the mower (`can_manage_mowers`, like a firmware update) and the plan feature `control`; without it the entities are not created. The mower's controller checks the value against the declaration; a refused change raises an error with MowDeck's reason (see below).
- While the non-working hours are off, setting their start or end switches them on with the other bound from the last window seen (else one hour apart). Start and end must differ.
- A setting never makes the mower move.

**Refused commands.** Fleet accepts a command before the mower's controller checks it against the mower's state. For the settings the integration waits up to 10 s for the controller's verdict over the Realtime WebSocket and raises an error that names MowDeck's reason when the command is refused (`rejected`, `failed` or `expired`). Without a connected WebSocket, or without a verdict in time, the command counts as sent; `event.luba_command` still shows what became of it.

## Map and heatmaps

The pictures are rendered in Home Assistant as SVG from MowDeck's data, without any extra package, in MowDeck's colours, north up, in the map's local metres:

- **`image.luba_map`**: mapped areas (green), no-go zones (red), obstacles (grey), transfer paths (dashed), the dock (blue square with its heading), the mower (an arrow in its heading, a dot without one) and, while a job runs, its planned path from `GET /telemetry/mowers/{id}/path`: the mowed part solid, the rest faint. It is rendered again only when what it shows changes (map revision, the mower's position to 25 cm or its heading to 5°, the path or its progress) and at most every 10 s; the path is read at most every 30 s while a job runs.
- **Heatmap images** (disabled by default): the layers of `GET /telemetry/mowers/{id}/heatmap` over the last 7 days, one square per cell, coloured like the web app's legend: coverage in five steps from grey (never cut) to green (always cut) with the missed spots outlined in red, RTK by the share of fixed positions (red below 50 %, amber below 90 %, green), Wi-Fi and mobile network from red (-90 dBm) through amber to green (-40 dBm).
- **`sensor.luba_missed_area`**: the missed spots' area in m².

Image entities show in a picture entity card or a picture glance card. Every image keeps its picture until its content changes, so a dashboard only fetches a new one after a change.

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

**Still image** (MowDeck 0.8 or newer): `Camera` shows MowDeck's still image of the mower's first camera (`GET /api/v1/fleet/mowers/{id}/camera/snapshot`) in picture cards, thumbnails and `camera.snapshot`. MowDeck takes it with the mower's own photo function or through the camera relay, keeping every camera limit (a sleeping mower is not woken, battery and Vision budget apply), and takes at most one per mower every 30 s. Because every image may start the mower's camera briefly, Home Assistant keeps one for 5 minutes and fetches a newer one in the background while the kept one is shown; only the very first request waits for it (up to 8 s). A mower that cannot take photos, a MowDeck before 0.8 or a token without the plan feature is asked again after an hour, an offline or sleeping mower after a minute. The further camera entities have no still image. The live stream is unaffected.

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

## Examples

Replace `luba` with the name of the mower and `notify.mobile_app_phone` with your phone's notify action (Home Assistant Companion app). The MowDeck links in notifications open the web app, where proposals are approved and the mower can be driven.

**Rain: send the mower home.** `lawn_mower.dock` pauses the job and returns to the dock; **Start mowing** later resumes it. `binary_sensor.luba_rain` is the mower's own rain sensor (only while the mower's **Rain detection** is on); `binary_sensor.luba_rain_hold` is MowDeck's rain pause from the forecast and recent rain.

```yaml
alias: "Luba: home when it rains"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.luba_rain
    to: "on"
  - trigger: state
    entity_id: binary_sensor.luba_rain_hold
    to: "on"
conditions:
  - condition: state
    entity_id: lawn_mower.luba
    state: mowing
actions:
  - action: lawn_mower.dock
    target:
      entity_id: lawn_mower.luba
```

**Alarms on the phone.** The bus event `mowdeck_notification` carries every MowDeck notification of the account; `url` is the path in the web app.

```yaml
alias: "MowDeck: critical alarms to the phone"
mode: queued
triggers:
  - trigger: event
    event_type: mowdeck_notification
    event_data:
      severity: critical
actions:
  - action: notify.mobile_app_phone
    data:
      title: "{{ trigger.event.data.title }}"
      message: "{{ trigger.event.data.body }}"
      data:
        # iOS opens url, Android clickAction.
        url: "https://mowdeck.example.ts.net{{ trigger.event.data.url or '/' }}"
        clickAction: "https://mowdeck.example.ts.net{{ trigger.event.data.url or '/' }}"
        push:
          interruption-level: time-sensitive
```

The same per mower with the event entity, for a few kinds only:

```yaml
alias: "Luba: stuck or lifted"
triggers:
  - trigger: state
    entity_id: event.luba_notification
    not_to: unavailable
conditions:
  - condition: state
    entity_id: event.luba_notification
    attribute: event_type
    state:
      - mower_stuck
      - mower_lifted
      - mower_left_dock_unexpectedly
      - geofence_exit
actions:
  - action: notify.mobile_app_phone
    data:
      title: "{{ state_attr('event.luba_notification', 'title') }}"
      message: "{{ state_attr('event.luba_notification', 'body') }}"
```

**A Vision proposal on the phone, with a button that opens it in MowDeck.** The event `mowdeck_vision_proposal` fires for every proposal change; `link` is the proposal's page in MowDeck (absolute), where it is approved.

```yaml
alias: "Luba: AI recovery proposal"
mode: single
triggers:
  - trigger: event
    event_type: mowdeck_vision_proposal
    event_data:
      state: proposed
actions:
  - action: notify.mobile_app_phone
    data:
      title: "Luba is stuck: proposal waiting"
      message: >-
        {{ state_attr('sensor.luba_ai_recovery', 'situation') }}:
        {{ state_attr('sensor.luba_ai_recovery', 'action') }}
      data:
        image: /api/image_proxy/image.luba_ai_recovery_image
        url: "{{ trigger.event.data.link }}"
        clickAction: "{{ trigger.event.data.link }}"
        actions:
          - action: URI
            title: Open in MowDeck
            uri: "{{ trigger.event.data.link }}"
```

**Skip the next run while people are in the garden.** Pressing **Skip next run of Morning** keeps the schedule; the run after it fires as usual.

```yaml
alias: "Garden party: skip the next mowing"
triggers:
  - trigger: state
    entity_id: calendar.family
    attribute: message
    to: Garden party
actions:
  - action: button.press
    target:
      entity_id: button.luba_skip_next_run_of_morning
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

## Maintenance and emergency nudges

MowDeck 0.11 sends a few one-shot device actions next to mowing ([ADR 0048](https://github.com/lukislp/mowdeck/blob/main/docs/adr/0048-maintenance-commands.md)). The connection lists the ones it supports (`MowerCapabilities.maintenance`), and only those get a button:

| Button | Command | Allowed when (checked by MowDeck) | Permission |
| --- | --- | --- | --- |
| Leave the dock | `leave_dock` | in the dock (docked or charging), no error, no command waiting for its confirmation | `mower.control` |
| Restart mower | `restart_mower` | at rest (docked, charging or idle), no error, no drive session, no command waiting for its confirmation; the mower is offline while it boots | `fleet.manage` |
| Read map again | `sync_map` | not while a manual drive session is open; MowDeck publishes the new map at once | `fleet.manage` |
| Relocate charging station | `relocate_dock` | in the dock, no job, no error; the mower forgets the stored dock position and learns it again (after the station was moved) | `fleet.manage` |
| Read RTK and dock position again | `resync_rtk` | always (no motion), except during a firmware update | `fleet.manage` |

All five need the plan feature `control`. Leaving the dock is ordinary control (it is what a start does first), so it is enabled and has no entity category. The other four reset or reread parts of the mower: they are configuration entities, disabled by default. Enable them under the device's entities when needed.

**Emergency nudges** (as in Mammotion-HA): four buttons that move a stuck mower a little. Each press sends one `manual_drive` frame, `linear` or `angular` 0.4 for `ttl_ms` 500 with the blades off; MowDeck's dead-man timer stops the wheels after 500 ms. Left turns are positive.

- Only for mowers MowDeck can drive (`manual_drive`); **Emergency nudge back** only where the mower may reverse (`drive_reverse`). Over the Mammotion cloud alone `manual_drive` is false (MowDeck drives Mammotion mowers over the Bluetooth bridge), so the nudges stay hidden.
- Configuration, disabled by default; need `mower.drive` on the mower and the plan feature `control` (like every command but the emergency stop).
- MowDeck refuses a nudge during a sensor stop (lift, tilt, collision), while the mower mows or returns (pause it first), while a start or return waits for its confirmation, and during a spot run, drive calibration or firmware update. A drive-recoverable error (stuck, wheel slip) is the case they are for and is accepted.

**Remote drive is not offered.** Mammotion-HA's remote drive switch opens a vendor session that its nudge buttons then use. In MowDeck a drive session is a Realtime WebSocket session (`drive_start`, `drive_frame` about ten times a second, `drive_stop`) that needs a live joystick: the dead-man timer stops the mower within a second unless frames keep coming. Home Assistant has no joystick to feed it, and a switch that keeps a session open would only send zero frames while blocking other drivers (Vision recovery, the web app) with `drive_busy`. The nudges already open and close a drive session per press, so nothing is lost; drive the mower live in the MowDeck web app.

**Refusals.** Fleet accepts a command before the mower's controller checks these rules. The integration waits up to 10 s for the controller's verdict over the Realtime WebSocket and raises an error with MowDeck's reason when the command is refused (`rejected`, `failed` or `expired`), for example "The mower refused restart_mower (rejected): the mower is mowing; a restart needs it docked, charging or idle (end the job first)". Without a connected WebSocket, or without a verdict in time, the press counts as sent; `event.luba_command` still shows what became of it.

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
- **"MowDeck refuses the heatmaps" (Repairs)**: the heatmap layers answer 403 for the token; grant `mower.view`. The map image keeps working, the heatmap images and the missed area are unavailable until the next successful update.
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
