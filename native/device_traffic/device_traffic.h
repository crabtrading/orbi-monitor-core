#ifndef ORBI_MONITOR_CORE_DEVICE_TRAFFIC_H
#define ORBI_MONITOR_CORE_DEVICE_TRAFFIC_H

#include <linux/types.h>

#define DEVICE_TRAFFIC_MAX_V4_PREFIXES 8
#define DEVICE_TRAFFIC_MAX_V6_PREFIXES 8
#define DEVICE_TRAFFIC_MAX_MAC_ENTRIES 4096
#define DEVICE_TRAFFIC_MAX_IP_ENTRIES 8192

struct device_traffic_mac_key {
  __u8 mac[6];
};

struct device_traffic_mac_value {
  __u64 upload_bytes_v4;
  __u64 download_bytes_v4;
  __u64 upload_bytes_v6;
  __u64 download_bytes_v6;
  __u64 packet_count;
  __u64 last_seen_ns;
};

struct device_traffic_ip_key {
  __u8 family;
  __u8 reserved[3];
  __u8 addr[16];
};

struct device_traffic_ip_value {
  __u8 observed_mac[6];
  __u8 reserved[2];
  __u64 upload_bytes;
  __u64 download_bytes;
  __u64 packet_count;
  __u64 last_seen_ns;
};

struct device_traffic_v4_prefix {
  __u32 network;
  __u32 mask;
};

struct device_traffic_v6_prefix {
  __u8 addr[16];
  __u32 prefix_len;
};

struct device_traffic_config {
  __u32 v4_count;
  __u32 v6_count;
  struct device_traffic_v4_prefix v4[DEVICE_TRAFFIC_MAX_V4_PREFIXES];
  struct device_traffic_v6_prefix v6[DEVICE_TRAFFIC_MAX_V6_PREFIXES];
};

#endif
