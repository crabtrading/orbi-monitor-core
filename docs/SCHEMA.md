# Schema Reference

This document describes the JSON shape returned by:

- `RouterSnapshot.to_dict()`
- the `orbi-monitor-core` CLI

Field meanings are based on the data exposed by Orbi AJAX and SOAP endpoints on `RBR750/RBS750`.

## Top-level fields

### `internet`

Object describing current internet state.

| Field | Type | Source | Meaning |
| --- | --- | --- | --- |
| `code` | integer | AJAX `basicStatus.cgi` | Router internet status code. `0` usually means internet is up. |
| `heading` | string | AJAX `basicStatus.cgi` | Heading shown by the router UI, typically `STATUS`. |
| `text` | string | AJAX `basicStatus.cgi` | Human-readable internet state, for example `GOOD`. |

### `devices`

Array of currently attached client devices.

### `satellites`

Array of currently visible satellites.

### `target_satellite_name`

Optional user-provided satellite name used by your own monitoring logic.  
The library does not require it for collection, but it is kept in the snapshot so downstream code can track one preferred satellite.

### `expected_connection`

Optional user-provided expected backhaul type, commonly `Wired`.

### `current_setting`

Dictionary built from:

- `http://ROUTER_IP/currentsetting.htm`

This usually contains transport-level metadata such as:

- `Firmware`
- `Model`
- `SOAPVersion`
- `LoginMethod`
- `SOAP_HTTPs_Port`

### `router_info`

Dictionary parsed from:

- `DeviceInfo:1#GetInfo`

Typical fields include:

- `ModelName`
- `DeviceName`
- `SerialNumber`
- `Firmwareversion`
- `Hardwareversion`
- `DeviceMode`
- `DeviceNameUserSet`

### `support_features`

Dictionary parsed from:

- `DeviceInfo:1#GetSupportFeatureListXML`

Typical keys include:

- `AttachedDevice`
- `SatelliteInfo`
- `SmartConnect`
- `DeviceTypeIcon`
- `SupportWPA3`

### `sources`

Raw parsed action outputs, grouped by source.

Current structure:

- `sources.ajax.basic_status`
- `sources.ajax.attached_devices`
- `sources.ajax.current_setting`
- `sources.soap.get_info`
- `sources.soap.get_support_feature_list_xml`
- `sources.soap.get_attach_device2`
- `sources.soap.get_current_satellites`
- `sources.soap.get_all_satellites`
- `sources.soap.get_missing_satellites`
- `sources.soap.get_current_satellites_wifi_info`
- `sources.soap_errors`

This section exists so downstream tools can consume fields that are not yet normalized into the stable device or satellite models.

## Device fields

Each item in `devices[]` has the following structure.

| Field | Type | Source | Meaning |
| --- | --- | --- | --- |
| `mac` | string | AJAX, SOAP | Client MAC address. SOAP is preferred if valid. |
| `name` | string | AJAX, SOAP | Client name. SOAP is preferred because it usually matches what the Orbi app shows. |
| `ip` | string | AJAX, SOAP | Current IPv4 address. |
| `name_user_set` | boolean or null | SOAP | Whether the device name was user-assigned in the Orbi app. |
| `type_name` | string | AJAX | Friendly device type from the AJAX endpoint, for example `Smart Phone` or `IP Camera`. |
| `model` | string | AJAX, SOAP | Device model if the router knows it. SOAP is preferred. |
| `model_user_set` | boolean or null | SOAP | Whether the model was manually set or confirmed. |
| `brand` | string | SOAP | Vendor/brand string, for example `Apple` or `Bosch`. May be empty. |
| `connected_orbi` | string | AJAX | Which node currently owns the client, for example `Orbi Router`, `Satellite A`, or another satellite name. |
| `connected_orbi_mac` | string | AJAX, SOAP | MAC address of the node/AP the client is associated with. AJAX is preferred; SOAP `ConnAPMAC` is used as fallback. |
| `ap_mac` | string | SOAP | Raw AP MAC returned by `GetAttachDevice2`. Useful when you want to map a device to a specific node radio. |
| `connection_type` | string | AJAX, SOAP | Normalized connection type such as `Wired`, `2.4 GHz`, `2.4 GHz - IoT`, or `5 GHz`. SOAP is preferred. |
| `ssid` | string | SOAP | SSID currently used by the client. Empty for some wired clients or when the router does not report it. |
| `signal_strength` | integer or null | SOAP | Client signal metric returned by Orbi. This is router-provided quality data and should not be assumed to be RSSI in dBm. |
| `linkspeed_mbps` | integer or null | SOAP | Current client link rate as reported by Orbi. Typically Mbps. |
| `allow_or_block` | string | SOAP | Access-control state, commonly `Allow`. |
| `schedule_enabled` | boolean or null | SOAP | Whether the client has a schedule applied. |
| `device_type` | integer or null | SOAP | Numeric Orbi device type identifier. |
| `device_type_user_set` | boolean or null | SOAP | Whether the device type was manually set. |
| `device_type_v2` | string | SOAP | Newer symbolic device type, for example `MEDIA_PLAYER` or `MOBILE`. |
| `device_type_name_v2` | string | SOAP | Human-readable version of `device_type_v2` when provided. |
| `upload` | integer or null | SOAP | Current upload metric from Orbi. The exact unit is not fully documented. |
| `download` | integer or null | SOAP | Current download metric from Orbi. The exact unit is not fully documented. |
| `qos_priority` | integer or null | SOAP | Router QoS priority for the device. |
| `grouping` | integer or null | SOAP | Grouping value returned by Orbi. |
| `schedule_period` | integer or null | SOAP | Schedule period value returned by Orbi. |
| `status` | string | AJAX | Raw device status from the AJAX endpoint. The exact values are firmware-specific. |

## Satellite fields

Each item in `satellites[]` has the following structure.

| Field | Type | Source | Meaning |
| --- | --- | --- | --- |
| `name` | string | AJAX, SOAP | Satellite name. SOAP `DeviceName` is preferred. |
| `ip` | string | AJAX, SOAP | Satellite management IP. |
| `mac` | string | AJAX, SOAP | Satellite MAC address. SOAP is preferred if valid. |
| `model` | string | AJAX, SOAP | Satellite model, for example `RBS750`. |
| `serial_number` | string | SOAP | Satellite serial number. |
| `firmware_version` | string | SOAP | Satellite firmware version from `FWVersion`. |
| `device_name_user_set` | boolean or null | SOAP | Whether the satellite name was user-assigned. |
| `connected_orbi` | string | AJAX | Parent node label from the AJAX endpoint. |
| `connected_orbi_mac` | string | AJAX, SOAP | Parent node MAC. SOAP `ParentMac` is preferred. |
| `connection_type` | string | AJAX, SOAP | Normalized backhaul type. In practice this is often `Wired` or `5 GHz`. SOAP `BHConnType` is preferred. |
| `backhaul_status` | string | AJAX | Human-readable backhaul health string from AJAX, for example `Good`. |
| `backhaul_conn_status` | integer or null | SOAP | Raw numeric backhaul status from `BHConnStatus`. |
| `signal_strength` | integer or null | SOAP | Satellite signal metric from `GetAllSatellites`. |
| `hop` | integer or null | SOAP | Reported mesh hop count. |
| `parent_mac` | string | SOAP | Parent node MAC address from the SOAP satellite payload. |
| `lighting_led_supported` | integer or null | SOAP | Whether the satellite reports lighting LED support. |
| `lighting_led_on_off_status` | integer or null | SOAP | Raw LED on/off status value. |
| `lighting_led_brightness_status` | integer or null | SOAP | Raw LED brightness status value. |
| `avs_support` | string | SOAP | AVS support string returned by the satellite payload. |
| `status` | string | AJAX | Raw online/offline status from AJAX. |
| `healthy` | boolean | Derived | True when `status` does not look offline. This is computed locally, not returned by the router. |

## Normalization rules

The library normalizes connection labels so downstream code does not have to deal with many firmware variants.

Examples:

| Raw value | Normalized value |
| --- | --- |
| `wired` | `Wired` |
| `5GHz` | `5 GHz` |
| `2.4GHz - IoT` | `2.4 GHz - IoT` |
| `2.4 IoTHz Wireless` | `2.4 GHz - IoT` |
| `ethernet` | `Wired` |

## Data source preference

When the same field exists in both AJAX and SOAP:

1. SOAP is preferred for richer device and satellite detail.
2. AJAX is used as fallback when SOAP is missing or unavailable.

This is why names, signal, SSID, and linkspeed are generally better in the merged output than in plain AJAX alone.
