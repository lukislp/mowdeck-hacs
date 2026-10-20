# Changelog

Release notes are generated from the commit messages by semantic-release and published with each
[GitHub release](https://github.com/lukislp/mowdeck-hacs/releases).

The integration was moved here from the `integrations/home-assistant` folder of
[lukislp/mowdeck](https://github.com/lukislp/mowdeck) on 2026-10-11.

## Before the first release

- Follows the current MowDeck API: the activity `updating`, the link state (`connected`) for Online, `min_speed_mps`, firmware updates, the manual drive speed setting, health totals, the Bluetooth bridge signal, the error category, source and `drive_recoverable`, and the position source.
- Speeds are rounded to two decimals in the sensor, the mowing speed number (step 0.01 m/s) and the `set_speed` action; blade height, battery and RSSI are whole numbers, the heading has one decimal.
- The 403 message names the permission the command needs.
- `documentation` and `issue_tracker` in the manifest point to this repository.
