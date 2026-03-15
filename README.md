# orbi-monitor-core

Lightweight backend-only collector for Netgear Orbi RBR750/RBS750.

This project exposes the useful router data that is available through:

- `POST /ajax/basicStatus.cgi`
- `POST /ajax/get_attached_devices`
- Netgear app SOAP over `https://ROUTER_IP:443/soap/server_sa/`

It is designed for people who want to build their own dashboards, automations, exporters, or alerts without depending on the stock Orbi UI.

## What it returns

- Internet status
- Current router settings from `currentsetting.htm`
- Router metadata from `GetInfo`
- Feature map from `GetSupportFeatureListXML`
- Attached clients
- Client node mapping (`ConnectedOrbi`)
- Client `SSID`
- Client `Linkspeed`
- Client `SignalStrength`
- Satellite list
- Satellite backhaul type (`BHConnType`)
- Satellite signal strength
- Parsed raw action outputs under `sources.ajax` and `sources.soap`

## Supported hardware

The implementation has been verified against:

- `RBR750`
- `RBS750`
- firmware `V7.2.8.2_5.1.18`

Other Orbi models may work if they expose the same AJAX and SOAP actions.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Usage

```bash
orbi-monitor-core \
  --host http://192.168.1.1 \
  --username admin \
  --password 'your-router-password' \
  --target-satellite-name satellite-a \
  --pretty
```

Example output:

```json
{
  "internet": {
    "code": 0,
    "heading": "STATUS",
    "text": "GOOD"
  },
  "devices": [
    {
      "name": "Media Speaker",
      "connection_type": "5 GHz",
      "signal_strength": 57,
      "linkspeed_mbps": 72,
      "ssid": "HOME_WIFI_5G"
    }
  ],
  "satellites": [
    {
      "name": "satellite-a",
      "connection_type": "Wired",
      "signal_strength": 6
    }
  ]
}
```

## Python API

```python
from orbi_monitor_core import OrbiClient

client = OrbiClient("http://192.168.1.1", "admin", "your-router-password")
snapshot = client.fetch_snapshot(
    target_satellite_name="satellite-a",
    expected_connection="Wired",
)

print(snapshot.target_satellite.name)
print(snapshot.target_satellite.connection_type)
print(snapshot.devices[0].signal_strength)
```

## Field reference

Complete field documentation lives in [docs/SCHEMA.md](docs/SCHEMA.md).

That document covers:

- top-level snapshot structure
- every `devices[]` field
- every `satellites[]` field
- `current_setting`, `router_info`, `support_features`, and `sources`
- normalization rules
- AJAX vs SOAP source preference

## Validated SOAP actions

Verified hidden SOAP methods are listed in [docs/VALIDATED_SOAP_ACTIONS.md](docs/VALIDATED_SOAP_ACTIONS.md).

That document covers:

- login transport and endpoint details
- validated device actions
- validated satellite actions
- fields seen in real responses
- known misleading paths such as `deviceinfo.cgi`

## Reverse engineering workflow

A reproducible discovery workflow is documented in [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md).

That document covers:

- how to start from `currentsetting.htm`
- how to inspect AJAX and SOAP safely
- how to use firmware strings to discover likely actions
- how to validate new actions before merging them into a collector

## Notes

- `SignalStrength` exposed by SOAP is the router-provided quality metric, not guaranteed to be RSSI in dBm.
- This project does not ship any frontend, deployment system, or cloud integration.
- No passwords, tokens, domains, or private configs are included in this repository.

## License

MIT
