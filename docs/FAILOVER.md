# Linux Failover Controller

`orbi-monitor-core` includes an optional Linux failover controller for deployments where:

- Orbi is in `AP mode`
- a Linux host is the active router
- NetworkManager manages the uplink connections
- failover should be driven by health checks and route metric changes

This controller is intentionally generic. It does **not** include:

- cloud tunnel recovery
- notification integrations
- vendor-specific modem logic

It focuses on the portable core:

- discover or resolve primary and failover WAN connections
- health-check each uplink with ICMP probes
- switch preferred default route using `ip route replace`
- persist decision state across runs
- emit structured upstream JSON that can be fed into dashboards or device-traffic payloads

## Components

- `orbi_monitor_core/networking.py`
  - reads `nmcli device status`
  - resolves default gateways by interface
  - optionally auto-discovers a failover candidate
- `orbi_monitor_core/failover.py`
  - decision engine
  - route metric application
  - state persistence
  - upstream status output

## Policy model

The controller uses a two-WAN model:

- primary WAN
- failover WAN

State transitions:

- `primary` -> `failover`
  - happens after `failure_threshold` consecutive primary health-check failures
  - only if the failover WAN is itself healthy
- `failover` -> `primary`
  - happens after `recovery_threshold` consecutive primary health-check successes

Health checks are simple by design:

- ICMP ping to one or more public targets
- optional interface binding with `ping -I`

## Route application

The controller keeps both default routes present when possible and changes preference using metrics:

- primary preferred:
  - primary metric = `primary_metric`
  - failover metric = `standby_metric`
- failover preferred:
  - primary metric = `primary_metric`
  - failover metric = `failover_metric`

This means failover can be activated by making the backup route strictly more preferred than the primary one.

## CLI

Run one policy cycle:

```bash
orbi-monitor-failover \
  --primary-connection "Wired connection 1" \
  --failover-connection "Wired connection 2" \
  --primary-label "Cable WAN" \
  --failover-label "USB Tether" \
  --check-target 1.1.1.1 \
  --check-target 8.8.8.8 \
  --pretty
```

Emit current upstream view only:

```bash
orbi-monitor-failover \
  --primary-connection "Wired connection 1" \
  --failover-connection "Wired connection 2" \
  --mode status \
  --pretty
```

## Output

Example payload:

```json
{
  "checked_at": "2026-03-17T20:00:03+00:00",
  "action": "switch_to_failover",
  "active_mode": "failover",
  "primary_available": true,
  "primary_healthy": false,
  "failover_available": true,
  "failover_healthy": true,
  "reason": "switched to failover WAN",
  "mode": "failover_wan",
  "active_label": "USB Tether",
  "last_switch_at": "2026-03-17T20:00:03+00:00",
  "sources": [
    {
      "id": "primary_wan",
      "label": "Cable WAN",
      "status": "Standby"
    },
    {
      "id": "failover_wan",
      "label": "USB Tether",
      "status": "Active"
    }
  ],
  "failover": {
    "active_mode": "failover",
    "last_reason": "switched to failover WAN"
  }
}
```

This output is designed to be directly consumable by:

- a custom dashboard
- `orbi-monitor-device-traffic --upstream-json ...`
- a Prometheus textfile exporter wrapper
- your own timer or watchdog service

## Limitations

- Linux-only
- assumes `nmcli` and `ip` are available
- assumes NetworkManager manages the uplinks
- does not manage DNS failover policy
- does not recover application-layer services after a WAN switch
- does not classify or validate captive portal states

## Recommended composition

For a typical Linux soft-router stack:

1. `orbi-monitor-failover` decides which uplink should be preferred
2. your own timer/service runs it every `30-60s`
3. `orbi-monitor-device-traffic` reads native traffic socket data
4. the upstream JSON produced by the failover controller is passed into the traffic normalizer
5. your dashboard renders:
   - active uplink
   - failover state
   - per-device traffic
