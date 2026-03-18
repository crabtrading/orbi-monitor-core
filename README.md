# orbi-monitor-core

Build your own Orbi observability stack without depending on the stock UI.

`orbi-monitor-core` is a backend-first toolkit for Netgear Orbi networks. It gives you:

- structured router and satellite state from hidden AJAX and SOAP endpoints
- normalized attached-client data for your own dashboards and automations
- optional host-side throughput estimation
- optional native per-device traffic telemetry using `tc + eBPF + libbpf`

It is designed for people who want raw data, stable schemas, and code-level control.

![Router topology overview](docs/assets/router-topology-overview.svg)

## Why this project exists

Orbi exposes useful state, but not in a form that is easy to reuse. The stock UI is fine for manual checks, but weak if you want to:

- build a custom dashboard
- export data into Prometheus, SQLite, or your own APIs
- automate alerts and topology checks
- compare WAN throughput against node-side probe throughput
- add Linux-side network telemetry when Orbi is running as `AP mode`

This project turns those hidden router responses into a clean Python API and CLI, then adds an optional native collector for real routed device traffic.

## What you get

- Internet status from `POST /ajax/basicStatus.cgi`
- attached device inventory from `POST /ajax/get_attached_devices`
- router metadata from hidden SOAP actions such as `GetInfo`
- support feature map from `GetSupportFeatureListXML`
- satellite inventory and backhaul state
- per-device fields such as:
  - `ConnectedOrbi`
  - `SSID`
  - `Linkspeed`
  - `SignalStrength`
- parsed raw response sources for debugging and reverse engineering
- optional local throughput estimate with `ping + iperf3 + speedtest`
- optional native device traffic collector that exports:
  - live `download_bps`
  - live `upload_bps`
  - per-device daily totals

## Architecture

The repository is intentionally split into two layers:

- `orbi_monitor_core.client`
  - AJAX + SOAP collector
  - normalizes Orbi router, satellite, and client state
- `native/device_traffic`
  - Linux-only collector
  - attaches `tc` eBPF programs on the LAN interface
  - reads pinned maps through `libbpf`
  - exports snapshots over a Unix domain socket

That separation keeps the Orbi collector reusable even if you do not want the Linux traffic telemetry layer.

## Supported hardware

Verified against:

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

Native collector build prerequisites:

- `clang`
- `llvm`
- `libbpf-dev`
- `libelf-dev`
- Linux headers matching the running kernel

## Quick start

Collect Orbi router state:

```bash
orbi-monitor-core \
  --host http://192.168.1.1 \
  --username admin \
  --password 'your-router-password' \
  --target-satellite-name satellite-a \
  --throughput-probe-host 192.168.50.10 \
  --pretty
```

Read native device traffic from the Unix socket:

```bash
orbi-monitor-device-traffic \
  --socket-path /run/orbi-monitor-core/device-traffic.sock \
  --dashboard-json ./snapshot.json \
  --pretty
```

## Example output

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
    "probe_host": "192.168.50.10",
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
    probe_host="192.168.50.10",
    probe_port=5201,
)

print(sample.lan_reverse_mbps)
print(sample.wan_download_mbps)
```

## Device traffic telemetry

The optional native collector is aimed at this deployment model:

- Orbi runs in `AP mode`
- a Linux host owns `LAN -> WAN` routing
- client traffic traverses a real Ethernet LAN interface

It uses:

- `tc ingress` and `tc egress`
- `eBPF` for authoritative byte accounting
- `libbpf` for direct pinned-map reads
- a Unix socket for lightweight snapshot delivery

It does not classify applications. That is intentionally deferred to a future DPI layer such as `nDPI`.

The implementation details live in [docs/DEVICE_TRAFFIC.md](docs/DEVICE_TRAFFIC.md).

## Documentation

- [docs/SCHEMA.md](docs/SCHEMA.md)
  - field reference for router, satellite, device, and source payloads
- [docs/VALIDATED_SOAP_ACTIONS.md](docs/VALIDATED_SOAP_ACTIONS.md)
  - hidden SOAP methods verified against real firmware
- [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md)
  - reproducible workflow for discovering and validating new actions
- [docs/DEVICE_TRAFFIC.md](docs/DEVICE_TRAFFIC.md)
  - native collector internals, socket schema, attribution model, and troubleshooting

## Design principles

- backend-first
- stable normalized schemas
- raw source preservation for debugging
- Linux telemetry kept separate from Orbi protocol collection
- no cloud dependency
- no frontend requirement

## Notes

- `SignalStrength` exposed by SOAP is a router-provided quality metric, not guaranteed to be RSSI in dBm.
- The throughput helper is a host-side estimate. If your measuring host is on Wi-Fi, it is not proof of wired backhaul truth.
- The native collector is Linux-only and expects the measurement host to be the active router for routed client traffic.
- Hardware offload and fast-path features may bypass `tc`; validate your deployment before trusting the counters.
- No passwords, tokens, domains, or private deployment configs are included in this repository.

## License

MIT
