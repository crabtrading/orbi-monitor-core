# Validated Hidden SOAP Actions

This document lists the SOAP actions that have been directly validated on:

- `RBR750`
- `RBS750`
- firmware `V7.2.8.2_5.1.18`

These actions are useful because they expose more detail than the normal AJAX endpoints.

## Transport

- Endpoint: `https://ROUTER_IP:443/soap/server_sa/`
- Session header: `SessionID`
- Login action: `urn:NETGEAR-ROUTER:service:DeviceConfig:1#SOAPLogin`

The router exposes the SOAP port through:

- `http://ROUTER_IP/currentsetting.htm`

On the validated firmware this includes:

- `SOAP_HTTPs_Port=443`
- `LoginMethod=2.0`

## Login

### `DeviceConfig:1#SOAPLogin`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceConfig:1#SOAPLogin` |
| Service | `DeviceConfig:1` |
| Purpose | Creates the authenticated session used by the other SOAP calls. |
| Status | Validated |

Body:

```xml
<M1:SOAPLogin xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceConfig:1">
  <Username>admin</Username>
  <Password>YOUR_PASSWORD</Password>
</M1:SOAPLogin>
```

Success is indicated by:

- `ResponseCode` of `000`
- a session cookie such as `sess_id=...`

## Validated device actions

### `DeviceInfo:1#GetAttachDevice2`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetAttachDevice2` |
| Service | `DeviceInfo:1` |
| Purpose | Returns the richer attached-client list used by the Orbi app. |
| Status | Validated |

Body:

```xml
<M1:GetAttachDevice2 xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />
```

Useful fields observed in live responses:

- `IP`
- `Name`
- `MAC`
- `ConnectionType`
- `SSID`
- `Linkspeed`
- `SignalStrength`
- `ConnAPMAC`
- `DeviceModel`
- `DeviceBrand`
- `DeviceTypeV2`

Notes:

- This is the main source for per-device signal and linkspeed in `orbi-monitor-core`.
- `SignalStrength` is a router-reported quality metric and should not automatically be interpreted as RSSI in dBm.

### `DeviceInfo:1#GetInfo`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetInfo` |
| Service | `DeviceInfo:1` |
| Purpose | Returns router metadata and basic status. |
| Status | Validated |

Useful fields observed:

- `ModelName`
- `DeviceName`
- `SerialNumber`
- `Firmwareversion`
- `Hardwareversion`
- `SignalStrength`

Notes:

- This is useful for router metadata, but not required for the current library merge logic.

### `DeviceInfo:1#GetSupportFeatureListXML`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetSupportFeatureListXML` |
| Service | `DeviceInfo:1` |
| Purpose | Returns the feature map supported by the router firmware. |
| Status | Validated |

Useful fields observed:

- `AttachedDevice: 3.0`
- `SatelliteInfo: 2.0`
- `DeviceTypeIcon: 2.5`
- `SmartConnect: 1.0`

Notes:

- This call is useful when deciding whether a model likely supports richer satellite/device methods.

## Validated satellite actions

### `DeviceInfo:1#GetCurrentSatellites`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetCurrentSatellites` |
| Service | `DeviceInfo:1` |
| Purpose | Returns the currently visible satellites with basic metadata. |
| Status | Validated |

Body:

```xml
<M1:GetCurrentSatellites xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />
```

Useful fields observed:

- `IP`
- `MAC`
- `DeviceName`
- `ModelName`
- `SignalStrength`
- `Hop`
- `ParentMac`

Notes:

- This is the simpler satellite list.
- It does not include the richer backhaul connection type fields exposed by `GetAllSatellites`.

### `DeviceInfo:1#GetAllSatellites`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetAllSatellites` |
| Service | `DeviceInfo:1` |
| Purpose | Returns the richer satellite list used by the library. |
| Status | Validated |

Body:

```xml
<M1:GetAllSatellites xmlns:M1="urn:NETGEAR-ROUTER:service:DeviceInfo:1" />
```

Useful fields observed:

- `IP`
- `MAC`
- `DeviceName`
- `ModelName`
- `SignalStrength`
- `Hop`
- `ParentMac`
- `BHConnType`
- `BHConnStatus`

Notes:

- This is the main source for satellite backhaul type in `orbi-monitor-core`.
- `BHConnType` has been observed returning values such as `wired` and `5GHz`.

### `DeviceInfo:1#GetMissingSatellites`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetMissingSatellites` |
| Service | `DeviceInfo:1` |
| Purpose | Returns satellites the router considers missing/offline. |
| Status | Validated |

Notes:

- On the validated live router this returned an empty list when all satellites were online.
- This is useful for alerting workflows.

### `DeviceInfo:1#GetCurrentSatellitesWIFIinfo`

| Item | Value |
| --- | --- |
| SOAPAction | `urn:NETGEAR-ROUTER:service:DeviceInfo:1#GetCurrentSatellitesWIFIinfo` |
| Service | `DeviceInfo:1` |
| Purpose | Returns additional backhaul Wi-Fi details. |
| Status | Validated |

Useful fields observed:

- `Hop`
- `BridgeMAC`
- `BHConnType`
- `BHRSSI`
- `BHMACaddress`
- `BHPhyTxRate`
- `BHPhyRxRate`
- `BHParentMAC`

Important note:

- On the validated firmware this did not behave like a clean per-satellite array.
- It returned a compact backhaul structure rather than a full list of satellite objects.
- Because of that, `orbi-monitor-core` currently does not merge this call into the published snapshot model.

## Failed or misleading paths

These are worth calling out because they are often mentioned online.

### `deviceinfo.cgi`

Status on validated firmware:

- reachable
- returns HTML
- does **not** return the rich JSON needed for monitoring on this router/firmware

### `wlan.cgi`

Status on validated firmware:

- reachable
- returns HTML
- not used by the library

### `sysinfo.cgi`

Status on validated firmware:

- reachable
- returns HTML
- not used by the library

## Practical recommendation

For `RBR750/RBS750`, the most useful monitoring combination is:

1. `basicStatus.cgi`
2. `get_attached_devices`
3. `DeviceInfo:1#GetAttachDevice2`
4. `DeviceInfo:1#GetAllSatellites`

That combination gives you:

- internet status
- node ownership
- client signal
- client linkspeed
- client SSID
- satellite backhaul type
- satellite signal
