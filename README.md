# orbi-monitor-core

Lightweight backend-only collector for Netgear Orbi RBR750/RBS750.

This project exposes the useful router data that is available through:

- `POST /ajax/basicStatus.cgi`
- `POST /ajax/get_attached_devices`
- Netgear app SOAP over `https://ROUTER_IP:443/soap/server_sa/`

It is designed for people who want to build their own dashboards, automations, exporters, or alerts without depending on the stock Orbi UI.

It also includes an optional local throughput helper for cases where you want to compare:

- WAN download speed from the measurement host
- probe download-like throughput to a node behind a satellite

It now also includes an optional **native device traffic collector** built with:

- `tc`
- `eBPF`
- `libbpf`
- Unix domain socket export

That collector is designed for hosts where Orbi runs in `AP mode` and a Linux router owns the LAN/WAN routing path.

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
- Optional local `ping + iperf3 + speedtest` throughput estimate

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

Native collector prerequisites:

- `clang`
- `llvm`
- `libbpf-dev`
- `libelf-dev`
- Linux headers matching the running kernel

## Usage

```bash
orbi-monitor-core \
  --host http://192.168.1.1 \
  --username admin \
  --password 'your-router-password' \
  --target-satellite-name satellite-a \
  --throughput-probe-host 192.168.1.31 \
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
  "throughput": {
    "probe_host": "192.168.1.31",
    "source_mode": "wifi_estimate",
    "lan_reverse_mbps": 185.4,
    "wan_download_mbps": 207.39,
    "status": "ok"
  },
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

Optional throughput probe:

```python
from orbi_monitor_core import measure_throughput

sample = measure_throughput(
    probe_host="192.168.1.31",
    probe_port=5201,
)

print(sample.lan_reverse_mbps)
print(sample.wan_download_mbps)
```

Optional device traffic socket reader:

```bash
orbi-monitor-device-traffic \
  --socket-path /run/orbi-monitor-core/device-traffic.sock \
  --dashboard-json ./snapshot.json \
  --pretty
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

## Device traffic collector

Native device traffic collector notes live in [docs/DEVICE_TRAFFIC.md](docs/DEVICE_TRAFFIC.md).

That document covers:

- `tc eBPF` scope and accounting model
- `libbpf` direct map reads
- socket output format
- attribution fallback rules
- build and run prerequisites

## Notes

- `SignalStrength` exposed by SOAP is the router-provided quality metric, not guaranteed to be RSSI in dBm.
- The optional throughput helper is a host-side estimate. If your measuring host is on Wi-Fi, it is not proof of wired backhaul truth.
- The optional device traffic collector is Linux-only and expects the measurement host to be the active router for routed client traffic.
- This project does not ship any frontend, deployment system, or cloud integration.
- No passwords, tokens, domains, or private configs are included in this repository.

## License

MIT
