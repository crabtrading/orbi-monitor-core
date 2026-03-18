# Device Traffic Collector

`orbi-monitor-core` includes an optional native collector that measures per-device routed traffic on a Linux LAN interface.

It is intended for deployments where:

- Netgear Orbi is running in `AP mode`
- a Linux host owns `LAN -> WAN` routing
- client traffic traverses a real Ethernet LAN interface that can see client MAC addresses

## Architecture

The collector is split into two pieces:

- `native/device_traffic/device_traffic.bpf.c`
  - `tc ingress/egress` programs
  - classifies traffic as upload/download using `LAN` vs `non-LAN` prefixes
  - keeps authoritative byte counters in pinned BPF maps
- `native/device_traffic/device_traffic.c`
  - loads and attaches the eBPF programs
  - reads maps through `libbpf`
  - applies smoothing and day accounting
  - exposes the current snapshot through a Unix domain socket

The Python helper in `orbi_monitor_core/device_traffic.py` is intentionally lightweight:

- read collector JSON from a Unix socket
- merge with router snapshot device metadata
- apply infrastructure MAC fallback rules
- emit normalized JSON for dashboards or exporters

## Environment Variables

The native collector reads these environment variables:

- `DEVICE_TRAFFIC_LAN_INTERFACE`
  - required
  - LAN interface name used for `tc`
- `DEVICE_TRAFFIC_LAN_SUBNETS_V4`
  - optional
  - comma-separated IPv4 CIDRs
  - example: `192.168.50.0/24,10.0.0.0/24`
- `DEVICE_TRAFFIC_LAN_PREFIXES_V6`
  - optional
  - comma-separated IPv6 prefixes
  - example: `fd00:50::/64,2001:db8:50::/64`
- `DEVICE_TRAFFIC_SOCKET_PATH`
  - optional
  - default: `/run/orbi-monitor-core/device-traffic.sock`
- `DEVICE_TRAFFIC_STATE_PATH`
  - optional
  - default: `/var/lib/orbi-monitor-core/device-traffic-state.json`
- `DEVICE_TRAFFIC_BPF_PIN_ROOT`
  - optional
  - default: `/sys/fs/bpf/orbi-monitor-core/device-traffic`
- `DEVICE_TRAFFIC_BPF_OBJECT`
  - optional
  - override compiled `.bpf.o` path
- `DEVICE_TRAFFIC_POLL_SECONDS`
  - optional
  - default: `3`
- `DEVICE_TRAFFIC_FLUSH_SECONDS`
  - optional
  - default: `10`
- `DEVICE_TRAFFIC_HOLD_SECONDS`
  - optional
  - default: `2`

`fe80::/10` is always treated as LAN-local.

## Build

```bash
cd native/device_traffic
make
```

This builds:

- `device_traffic`
- `build/device_traffic.bpf.o`

## Run

Example:

```bash
export DEVICE_TRAFFIC_LAN_INTERFACE=enx123456789abc
export DEVICE_TRAFFIC_LAN_SUBNETS_V4=192.168.50.0/24
export DEVICE_TRAFFIC_SOCKET_PATH=/run/orbi-monitor-core/device-traffic.sock

cd native/device_traffic
./device_traffic
```

## Read the socket

Raw socket reader:

```bash
orbi-monitor-device-traffic \
  --socket-path /run/orbi-monitor-core/device-traffic.sock \
  --pretty
```

Normalized output using a previously collected router snapshot:

```bash
orbi-monitor-core \
  --host http://192.168.1.1 \
  --username admin \
  --password 'your-router-password' \
  --pretty > snapshot.json

orbi-monitor-device-traffic \
  --socket-path /run/orbi-monitor-core/device-traffic.sock \
  --dashboard-json ./snapshot.json \
  --pretty
```

## Attribution rules

The collector records:

- MAC-based byte totals
- IP-based fallback observations

The Python normalizer prefers:

1. device MACs from the router snapshot
2. `dnsmasq` leases if provided
3. `ip neigh` on the LAN interface if provided

Traffic is treated as suspicious and moved into `unattributed` when:

- the MAC matches router/satellite/infrastructure metadata
- the MAC maps to multiple active IPs within a short time window
- the MAC matches a local interface on the Linux router

## Notes

- This collector does not classify applications.
- `nDPI` or similar DPI should be layered later as a separate classification stage.
- Hardware offload and fast-path features may bypass `tc`; validate your deployment before relying on the counters.
