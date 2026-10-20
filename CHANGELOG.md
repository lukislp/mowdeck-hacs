# Changelog

## Unreleased

- Follows the current MowDeck API: the activity `updating`, the link state (`connected`) for Online, `min_speed_mps`, firmware updates, the manual drive speed setting, health totals, the Bluetooth bridge signal, the error category, source and `drive_recoverable`, and the position source.
- Speeds are rounded to two decimals in the sensor, the mowing speed number (step 0.01 m/s) and the `set_speed` action; blade height, battery and RSSI are whole numbers, the heading has one decimal.
- The 403 message names the permission the command needs.
- `documentation` and `issue_tracker` in the manifest point to this repository.
- Moved from `lukislp/mowdeck` `integrations/home-assistant` at commit 666e09f749901974cb093b5c6eb950991537bcdb. The history of the integration before this point lives there.
