#include "device_traffic.h"

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/pkt_cls.h>
#include <linux/socket.h>
#include <linux/udp.h>
#include <linux/tcp.h>
#include <linux/if_vlan.h>

#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

#ifndef AF_INET
#define AF_INET 2
#endif

#ifndef AF_INET6
#define AF_INET6 10
#endif

struct router_monitor_vlan_hdr {
  __be16 h_vlan_tci;
  __be16 h_vlan_encapsulated_proto;
};

union router_monitor_ipv6_addr {
  __u8 bytes[16];
  __u32 words[4];
};

struct {
  __uint(type, BPF_MAP_TYPE_HASH);
  __uint(max_entries, DEVICE_TRAFFIC_MAX_MAC_ENTRIES);
  __type(key, struct device_traffic_mac_key);
  __type(value, struct device_traffic_mac_value);
  __uint(pinning, LIBBPF_PIN_BY_NAME);
} device_stats SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_HASH);
  __uint(max_entries, DEVICE_TRAFFIC_MAX_IP_ENTRIES);
  __type(key, struct device_traffic_ip_key);
  __type(value, struct device_traffic_ip_value);
  __uint(pinning, LIBBPF_PIN_BY_NAME);
} ip_stats SEC(".maps");

struct {
  __uint(type, BPF_MAP_TYPE_ARRAY);
  __uint(max_entries, 1);
  __type(key, __u32);
  __type(value, struct device_traffic_config);
  __uint(pinning, LIBBPF_PIN_BY_NAME);
} traffic_config SEC(".maps");

static __always_inline int ipv4_is_lan(const struct device_traffic_config *config, __u32 addr)
{
  int i;

#pragma unroll
  for (i = 0; i < DEVICE_TRAFFIC_MAX_V4_PREFIXES; i++) {
    if ((__u32)i >= config->v4_count)
      break;
    if ((addr & config->v4[i].mask) == config->v4[i].network)
      return 1;
  }
  return 0;
}

static __always_inline int ipv6_is_link_local(const __u8 *addr)
{
  return addr[0] == 0xfe && (addr[1] & 0xc0) == 0x80;
}

static __always_inline __u8 ipv6_byte_mask(__u32 bits)
{
  if (bits >= 8)
    return 0xffu;
  if (bits == 0)
    return 0u;
  return (__u8)(0xffu << (8 - bits));
}

static __always_inline int ipv6_match_prefix(const union router_monitor_ipv6_addr *addr,
                                             const struct device_traffic_v6_prefix *prefix)
{
  __u32 prefix_len = prefix->prefix_len;
  int i;

#pragma unroll
  for (i = 0; i < 16; i++) {
    __u32 byte_offset = (__u32)i * 8;
    __u32 bits;
    __u8 mask;

    if (prefix_len <= byte_offset)
      break;
    bits = prefix_len - byte_offset;
    if (bits > 8)
      bits = 8;
    mask = ipv6_byte_mask(bits);
    if ((addr->bytes[i] & mask) != (prefix->addr[i] & mask))
      return 0;
  }
  return 1;
}

static __always_inline int ipv6_is_lan(const struct device_traffic_config *config,
                                       const union router_monitor_ipv6_addr *addr)
{
  int i;

  if (ipv6_is_link_local(addr->bytes))
    return 1;

#pragma unroll
  for (i = 0; i < DEVICE_TRAFFIC_MAX_V6_PREFIXES; i++) {
    if ((__u32)i >= config->v6_count)
      break;
    if (ipv6_match_prefix(addr, &config->v6[i]))
      return 1;
  }
  return 0;
}

static __always_inline void update_mac_stats(const __u8 *mac, __u64 upload_bytes, __u64 download_bytes,
                                             __u64 upload_bytes_v4, __u64 download_bytes_v4,
                                             __u64 upload_bytes_v6, __u64 download_bytes_v6)
{
  struct device_traffic_mac_key key = {};
  struct device_traffic_mac_value *value;
  struct device_traffic_mac_value zero = {};

  __builtin_memcpy(key.mac, mac, sizeof(key.mac));
  value = bpf_map_lookup_elem(&device_stats, &key);
  if (!value) {
    zero.last_seen_ns = bpf_ktime_get_ns();
    bpf_map_update_elem(&device_stats, &key, &zero, BPF_NOEXIST);
    value = bpf_map_lookup_elem(&device_stats, &key);
    if (!value)
      return;
  }

  if (upload_bytes_v4)
    __sync_fetch_and_add(&value->upload_bytes_v4, upload_bytes_v4);
  if (download_bytes_v4)
    __sync_fetch_and_add(&value->download_bytes_v4, download_bytes_v4);
  if (upload_bytes_v6)
    __sync_fetch_and_add(&value->upload_bytes_v6, upload_bytes_v6);
  if (download_bytes_v6)
    __sync_fetch_and_add(&value->download_bytes_v6, download_bytes_v6);
  if (upload_bytes || download_bytes)
    __sync_fetch_and_add(&value->packet_count, 1);
  value->last_seen_ns = bpf_ktime_get_ns();
}

static __always_inline void update_ip_stats(const struct device_traffic_ip_key *key, const __u8 *mac,
                                            __u64 upload_bytes, __u64 download_bytes)
{
  struct device_traffic_ip_value *value;
  struct device_traffic_ip_value zero = {};

  value = bpf_map_lookup_elem(&ip_stats, key);
  if (!value) {
    __builtin_memcpy(zero.observed_mac, mac, sizeof(zero.observed_mac));
    zero.last_seen_ns = bpf_ktime_get_ns();
    bpf_map_update_elem(&ip_stats, key, &zero, BPF_NOEXIST);
    value = bpf_map_lookup_elem(&ip_stats, key);
    if (!value)
      return;
  }

  __builtin_memcpy(value->observed_mac, mac, sizeof(value->observed_mac));
  if (upload_bytes)
    __sync_fetch_and_add(&value->upload_bytes, upload_bytes);
  if (download_bytes)
    __sync_fetch_and_add(&value->download_bytes, download_bytes);
  if (upload_bytes || download_bytes)
    __sync_fetch_and_add(&value->packet_count, 1);
  value->last_seen_ns = bpf_ktime_get_ns();
}

static __always_inline int parse_ipv4(struct __sk_buff *skb, void *data, void *data_end,
                                      struct ethhdr *eth, __u64 nh_off,
                                      const struct device_traffic_config *config)
{
  struct iphdr *ip4 = data + nh_off;
  struct device_traffic_ip_key ip_key = {};
  const __u8 *local_mac = NULL;
  __u64 upload_bytes = 0;
  __u64 download_bytes = 0;
  int src_is_lan;
  int dst_is_lan;

  if ((void *)(ip4 + 1) > data_end)
    return TC_ACT_OK;

  src_is_lan = ipv4_is_lan(config, ip4->saddr);
  dst_is_lan = ipv4_is_lan(config, ip4->daddr);
  if (src_is_lan == dst_is_lan)
    return TC_ACT_OK;

  ip_key.family = AF_INET;
  if (src_is_lan) {
    local_mac = eth->h_source;
    upload_bytes = (__u64)skb->len;
    __builtin_memcpy(ip_key.addr, &ip4->saddr, sizeof(ip4->saddr));
    update_mac_stats(local_mac, upload_bytes, 0, upload_bytes, 0, 0, 0);
  } else {
    local_mac = eth->h_dest;
    download_bytes = (__u64)skb->len;
    __builtin_memcpy(ip_key.addr, &ip4->daddr, sizeof(ip4->daddr));
    update_mac_stats(local_mac, 0, download_bytes, 0, download_bytes, 0, 0);
  }

  update_ip_stats(&ip_key, local_mac, upload_bytes, download_bytes);
  return TC_ACT_OK;
}

static __always_inline int parse_ipv6(struct __sk_buff *skb, void *data, void *data_end,
                                      struct ethhdr *eth, __u64 nh_off,
                                      const struct device_traffic_config *config)
{
  struct ipv6hdr *ip6 = data + nh_off;
  struct device_traffic_ip_key ip_key = {};
  union router_monitor_ipv6_addr src_addr = {};
  union router_monitor_ipv6_addr dst_addr = {};
  const __u8 *local_mac = NULL;
  __u64 upload_bytes = 0;
  __u64 download_bytes = 0;
  int src_is_lan;
  int dst_is_lan;

  if ((void *)(ip6 + 1) > data_end)
    return TC_ACT_OK;

  __builtin_memcpy(src_addr.bytes, ip6->saddr.s6_addr, sizeof(src_addr.bytes));
  __builtin_memcpy(dst_addr.bytes, ip6->daddr.s6_addr, sizeof(dst_addr.bytes));

  src_is_lan = ipv6_is_lan(config, &src_addr);
  dst_is_lan = ipv6_is_lan(config, &dst_addr);
  if (src_is_lan == dst_is_lan)
    return TC_ACT_OK;

  ip_key.family = AF_INET6;
  if (src_is_lan) {
    local_mac = eth->h_source;
    upload_bytes = (__u64)skb->len;
    __builtin_memcpy(ip_key.addr, src_addr.bytes, sizeof(ip_key.addr));
    update_mac_stats(local_mac, upload_bytes, 0, 0, 0, upload_bytes, 0);
  } else {
    local_mac = eth->h_dest;
    download_bytes = (__u64)skb->len;
    __builtin_memcpy(ip_key.addr, dst_addr.bytes, sizeof(ip_key.addr));
    update_mac_stats(local_mac, 0, download_bytes, 0, 0, 0, download_bytes);
  }

  update_ip_stats(&ip_key, local_mac, upload_bytes, download_bytes);
  return TC_ACT_OK;
}

static __always_inline int handle_packet(struct __sk_buff *skb)
{
  void *data = (void *)(long)skb->data;
  void *data_end = (void *)(long)skb->data_end;
  struct ethhdr *eth = data;
  __u16 proto;
  __u64 nh_off = sizeof(*eth);
  __u32 key = 0;
  const struct device_traffic_config *config;

  if ((void *)(eth + 1) > data_end)
    return TC_ACT_OK;

  config = bpf_map_lookup_elem(&traffic_config, &key);
  if (!config)
    return TC_ACT_OK;

  proto = bpf_ntohs(eth->h_proto);
  if (proto == ETH_P_8021Q || proto == ETH_P_8021AD) {
    struct router_monitor_vlan_hdr *vh = data + nh_off;
    if ((void *)(vh + 1) > data_end)
      return TC_ACT_OK;
    proto = bpf_ntohs(vh->h_vlan_encapsulated_proto);
    nh_off += sizeof(*vh);
  }

  if (proto == ETH_P_IP)
    return parse_ipv4(skb, data, data_end, eth, nh_off, config);
  if (proto == ETH_P_IPV6)
    return parse_ipv6(skb, data, data_end, eth, nh_off, config);
  return TC_ACT_OK;
}

SEC("classifier/ingress")
int device_traffic_ingress(struct __sk_buff *skb)
{
  return handle_packet(skb);
}

SEC("classifier/egress")
int device_traffic_egress(struct __sk_buff *skb)
{
  return handle_packet(skb);
}

char LICENSE[] SEC("license") = "GPL";
