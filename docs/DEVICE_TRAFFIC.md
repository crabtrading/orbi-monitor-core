# Device Traffic Collector

`orbi-monitor-core` includes an optional native collector that measures per-device routed traffic on a Linux LAN interface.

It is intended for deployments where:

- Netgear Orbi is running in `AP mode`
- a Linux host owns `LAN -> WAN` routing
- client traffic traverses a real Ethernet LAN interface that can see client MAC addresses

![Router topology overview](assets/router-topology-overview.svg)

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

## Accounting model

The collector is intentionally opinionated:

- it measures **routed client traffic**
- it does **not** try to measure `LAN <-> LAN` transfers
- it does **not** do application classification
- it keeps byte accounting authoritative in the eBPF layer and leaves higher-level attribution to userspace

Upload/download is decided by address locality, not by hook direction alone:

- `src ∈ LAN prefixes` and `dst ∉ LAN prefixes` -> upload
- `dst ∈ LAN prefixes` and `src ∉ LAN prefixes` -> download
- `src` and `dst` both local -> ignored
- `src` and `dst` both non-local -> ignored

This is why the collector attaches to both `tc ingress` and `tc egress` but does not treat
`ingress == upload` or `egress == download` as a hard rule.

IPv6 `fe80::/10` is always treated as LAN-local even if no explicit IPv6 prefixes are configured.

## BPF maps

The collector uses three pinned maps:

- `device_stats`
  - key: `mac[6]`
  - value:
    - `upload_bytes_v4`
    - `download_bytes_v4`
    - `upload_bytes_v6`
    - `download_bytes_v6`
    - `packet_count`
    - `last_seen_ns`
- `ip_stats`
  - key:
    - `family`
    - `addr[16]`
  - value:
    - `observed_mac`
    - `upload_bytes`
    - `download_bytes`
    - `packet_count`
    - `last_seen_ns`
- `traffic_config`
  - array map containing LAN IPv4 and IPv6 prefix configuration

Why two stats maps:

- `device_stats` is the primary, low-cardinality device accounting view
- `ip_stats` is a secondary attribution aid for cases where the observed L2 MAC should not be trusted directly

This split keeps the fast path simple while still giving userspace enough information to detect infrastructure MACs,
bridged paths, or rapidly shifting `IP -> MAC` observations.

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

## Read path and `libbpf` behavior

Userspace reads pinned maps directly with `libbpf`.

The collector prefers batched reads:

- `bpf_map_lookup_batch()` for efficient iteration

But it does **not** assume batch support is perfectly reliable across kernels and map implementations.
If a batch read:

- fails
- returns a partial/ambiguous iteration state
- or otherwise looks inconsistent for the current poll

the collector resets iteration state and falls back to:

- `bpf_map_get_next_key()`
- `bpf_map_lookup_elem()`

for that poll cycle.

The collector will not publish a half-built snapshot for a failed read cycle.

## Smoothing and daily accounting

Polling is normally every `3s`.

Rate calculation rules:

- first sample for a key -> use raw delta-derived rate
- later samples -> apply exponential smoothing

Current smoothing:

- `smoothed = 0.7 * previous + 0.3 * current`

To avoid ugly UI drops during short collector gaps or uplink failover, the collector may briefly keep the
previous smoothed rate for up to `DEVICE_TRAFFIC_HOLD_SECONDS`.

Daily totals are stored by date key:

- `days[YYYY-MM-DD][MAC]`

The state file keeps:

- current day
- previous day
- running totals
- last seen timestamps

The collector flushes state periodically instead of on every packet.

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

## Socket payload shape

The native collector exposes raw JSON over the Unix socket.

Top-level fields:

- `checked_at`
- `poll_interval_seconds`
- `mac_items`
- `ip_items`

Example:

```json
{
  "checked_at": "2026-03-17T20:00:03Z",
  "poll_interval_seconds": 3,
  "mac_items": [
    {
      "mac": "AA:AA:AA:AA:AA:01",
      "download_bps": 64000.0,
      "upload_bps": 32000.0,
      "download_bytes_today": 2048,
      "upload_bytes_today": 1024,
      "total_bytes_today": 3072,
      "last_seen_at": "2026-03-17T20:00:03Z",
      "active": true
    }
  ],
  "ip_items": [
    {
      "family": "ipv4",
      "ip": "192.168.50.20",
      "observed_mac": "AA:AA:AA:AA:AA:01",
      "download_bps": 12000.0,
      "upload_bps": 0.0,
      "download_bytes_today": 4096,
      "upload_bytes_today": 0,
      "total_bytes_today": 4096,
      "last_seen_at": "2026-03-17T20:00:03Z",
      "active": true
    }
  ]
}
```

The Python normalizer converts that into a dashboard-friendly payload with:

- merged per-device `items`
- optional `upstream` block supplied by the caller
- an `unattributed` bucket for traffic that could not be safely mapped to an endpoint device

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

Operationally, the attribution pipeline is:

1. trust `device_stats` for direct endpoint MACs
2. compare against router snapshot infrastructure MACs
3. compare against local router interface MACs
4. look for short-window multi-IP instability in `ip_stats`
5. if the direct MAC is suspect, recover via:
   - router snapshot `devices[]`
   - optional `dnsmasq` leases
   - optional `ip neigh`
6. if still ambiguous, keep the bytes in `unattributed`

That conservative behavior is intentional; the library prefers under-attribution over confidently assigning
traffic to the wrong device.

## Operational limitations

This collector works best when:

- Orbi is in `AP mode`
- clients are bridged onto a real Ethernet LAN
- the Linux host is the only routed uplink

Be careful with:

- hardware offload / fast-path features that bypass `tc`
- additional bridges or repeaters that may hide the original client MAC
- VLAN designs where the chosen hook point no longer sees the true endpoint L2 identity
- privacy MAC rotation on some client operating systems

The collector does not currently try to merge randomized MAC identities into a single logical device.

## Troubleshooting

If the collector reports zeros or no devices:

1. confirm the LAN interface is correct:
   - `DEVICE_TRAFFIC_LAN_INTERFACE`
2. confirm the programs are attached:
   - `tc filter show dev <iface> ingress`
   - `tc filter show dev <iface> egress`
3. confirm maps are pinned:
   - `bpftool map show pinned /sys/fs/bpf/orbi-monitor-core/device-traffic`
4. confirm the socket exists:
   - `ls -l /run/orbi-monitor-core/device-traffic.sock`
5. inspect raw output first:
   - `orbi-monitor-device-traffic --socket-path ... --pretty`

If direct MAC attribution looks wrong:

1. inspect router/satellite MACs in the snapshot
2. pass a router snapshot to the Python normalizer
3. optionally provide a leases file and LAN interface for fallback resolution
4. check whether the suspect traffic is ending up in `unattributed`

## Notes

- This collector does not classify applications.
- `nDPI` or similar DPI should be layered later as a separate classification stage.
- Hardware offload and fast-path features may bypass `tc`; validate your deployment before relying on the counters.
