# Reverse Engineering Workflow

This document explains how to discover additional Orbi data sources in a safe, reproducible way.

Scope:

- understanding your own router
- discovering undocumented AJAX and SOAP actions
- validating fields before you depend on them

Out of scope:

- bypassing authentication
- attacking third-party devices
- exposing router internals to the public internet

Use this workflow only against hardware you own or administer.

## 1. Start with the easy entry points

Before extracting firmware or searching binaries, check what the router already exposes.

### `currentsetting.htm`

This is often the fastest way to learn how the app-facing SOAP transport is configured.

```bash
curl -s http://192.168.1.1/currentsetting.htm
```

Useful fields:

- `SOAP_HTTPs_Port`
- `SOAPVersion`
- `LoginMethod`
- `Model`
- `Firmware`

On validated `RBR750/RBS750` firmware this returned:

- `SOAP_HTTPs_Port=443`
- `LoginMethod=2.0`

That immediately tells you where the app SOAP endpoint lives.

### AJAX endpoints

Check whether the router already exposes useful JSON through the browser UI endpoints.

Common starting points:

- `POST /ajax/basicStatus.cgi`
- `POST /ajax/get_attached_devices`

These often require:

- admin credentials
- a valid `XSRF_TOKEN`
- `X-XSRF-TOKEN` request header

These AJAX calls are usually the easiest way to get:

- internet status
- node/device ownership
- satellite names

## 2. Verify popular online claims instead of trusting them

A lot of Orbi posts online repeat the same URLs without checking firmware differences.

Always test them on the exact router and firmware you have.

For example, on validated `RBR750 V7.2.8.2_5.1.18`:

- `deviceinfo.cgi` returned HTML, not the richer JSON many posts claim
- `wlan.cgi` returned HTML
- `sysinfo.cgi` returned HTML

That is why the library does not rely on those routes.

## 3. Inspect the stock web UI traffic

If the normal UI or mobile app shows information you want, first assume the router already exposes it somewhere.

Useful method:

1. Open the router UI in a browser.
2. Open developer tools.
3. Watch `Network` while loading pages.
4. Search for:
   - `ajax/`
   - `soap`
   - `satellite`
   - `attached`
   - `device`

Things to capture:

- request URL
- request method
- request headers
- response body

This often reveals:

- hidden AJAX routes
- XSRF handling
- field names the frontend expects

## 4. Discover SOAP support before guessing actions

Once you know the router has SOAP enabled, identify the real transport before trying random ports.

Recommended order:

1. read `currentsetting.htm`
2. confirm the SOAP port
3. confirm whether TLS is required
4. confirm `LoginMethod`

For validated `RBR750/RBS750`, the important facts were:

- port `443`
- TLS enabled
- login method `2.0`
- endpoint `/soap/server_sa/`

That is much more reliable than hard-coding common Netgear defaults like `5000`.

## 5. Log in first, then test one action at a time

Do not try to discover everything in one script at first.

Use this order:

1. login
2. call one known action
3. inspect raw XML
4. parse fields
5. only then merge into your collector

The first useful actions to validate are:

- `DeviceConfig:1#SOAPLogin`
- `DeviceInfo:1#GetAttachDevice2`
- `DeviceInfo:1#GetAllSatellites`

See [VALIDATED_SOAP_ACTIONS.md](VALIDATED_SOAP_ACTIONS.md) for the exact action names.

## 6. Read raw XML before designing your schema

Do not normalize too early.

When you discover a new action:

1. save the raw XML
2. inspect repeated containers
3. identify stable fields
4. only then define your public model

This avoids mistakes such as:

- treating a scalar response like a per-satellite array
- confusing a quality score with RSSI in dBm
- depending on fields that only appear on some firmware builds

`GetCurrentSatellitesWIFIinfo` is a good example:

- it was real
- it returned useful backhaul fields
- but it did not behave like a clean list of satellites

So it was documented, but not merged into the public snapshot model.

## 7. Prefer merged models, but keep source provenance

In practice, useful Orbi monitoring comes from combining:

1. AJAX for topology labels and browser-visible status
2. SOAP for richer device and satellite details

When merging:

- prefer SOAP for signal, linkspeed, SSID, model, brand
- prefer AJAX for browser-facing node names and general status
- keep field provenance documented

That is why this project keeps explicit docs for:

- [SCHEMA.md](SCHEMA.md)
- [VALIDATED_SOAP_ACTIONS.md](VALIDATED_SOAP_ACTIONS.md)

## 8. Search firmware strings when the UI is not enough

If the router UI hints that more data exists, but you cannot find the route from browser traffic alone, inspect firmware strings.

High-level workflow:

1. download the official firmware image for your exact model
2. extract the filesystem
3. search binaries and shell scripts for action names and field names

Useful search terms:

- `GetCurrentSatellites`
- `GetAllSatellites`
- `GetAttachDevice2`
- `SignalStrength`
- `BHRSSI`
- `BHPhyTxRate`
- `BHPhyRxRate`
- `ConnAPMAC`

On validated firmware, searching the extracted rootfs and the `httpd` binary directly revealed:

- `get_sta_signal_strength`
- `GetCurrentSatellitesWIFIinfo`
- `GetAllSatellites`
- `GetMissingSatellites`
- `BHRSSI`
- `BHPhyTxRate`
- `BHPhyRxRate`

That strongly narrowed the set of actions worth testing live.

## 9. Use firmware extraction as a guide, not as truth

Firmware strings tell you that a feature probably exists, not that it will behave the way you expect.

After finding a candidate action in the firmware:

1. validate it live
2. capture the raw response
3. compare it against your expectations

This is important because:

- some actions are compiled in but unavailable on your model
- some actions return partial structures
- some actions only work on one service namespace

Example:

- `GetCurrentSatellitesWIFIinfo` worked under `DeviceInfo:1`
- the same idea under other guessed service namespaces did not

## 10. Keep the process safe

Recommended guardrails:

- never expose the router SOAP endpoint to the public internet
- only test from your LAN or a secured management path
- do not enable debug services permanently if you do not need them
- do not commit router passwords, cookies, session IDs, or private domains into public repos
- sanitize live payloads before publishing examples

For public documentation:

- replace real passwords
- replace your personal domain names
- replace personal satellite labels if they reveal location or household details

## 11. Suggested workflow for new discoveries

When you think a new action exists, use this checklist:

1. Confirm router model and firmware.
2. Read `currentsetting.htm`.
3. Validate login over SOAP.
4. Call the action with minimal XML.
5. Save raw response.
6. Identify stable fields.
7. Compare against AJAX/front-end behavior.
8. Decide whether the result belongs in:
   - production snapshot model
   - experimental field docs only
9. Add tests using sanitized fixtures.
10. Document the action and its caveats.

## 12. Minimal test strategy

Before publishing support for a newly discovered action, add tests for:

- XML parsing
- field extraction
- normalization
- merge precedence when AJAX and SOAP disagree

This prevents a common failure mode in reverse-engineered integrations:

- working once with live data
- silently breaking after a small refactor

## 13. Practical recommendation

For most people working with `RBR750/RBS750`, this is the best order of effort:

1. AJAX routes
2. `currentsetting.htm`
3. SOAP login
4. `GetAttachDevice2`
5. `GetAllSatellites`
6. firmware string search for anything beyond that

That gets you most of the useful monitoring value without turning the project into a firmware archaeology exercise.
