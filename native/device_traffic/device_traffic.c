#define _GNU_SOURCE

#include "device_traffic.h"

#include <arpa/inet.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>


#define MAX_TIME_TEXT 40
#define MAX_IP_TEXT INET6_ADDRSTRLEN
#define SOCKET_BACKLOG 8

static volatile sig_atomic_t g_running = 1;

struct app_config {
  char iface[IF_NAMESIZE];
  char socket_path[PATH_MAX];
  char state_path[PATH_MAX];
  char pin_root[PATH_MAX];
  char bpf_object_path[PATH_MAX];
  int poll_seconds;
  int flush_seconds;
  int hold_seconds;
  struct device_traffic_config bpf_config;
};

struct mac_runtime_entry {
  struct device_traffic_mac_key key;
  struct device_traffic_mac_value prev_value;
  uint64_t day_upload_bytes;
  uint64_t day_download_bytes;
  double upload_bps;
  double download_bps;
  time_t last_nonzero_at;
  char last_seen_at[MAX_TIME_TEXT];
  bool initialized;
};

struct ip_runtime_entry {
  struct device_traffic_ip_key key;
  struct device_traffic_ip_value prev_value;
  uint64_t day_upload_bytes;
  uint64_t day_download_bytes;
  double upload_bps;
  double download_bps;
  time_t last_nonzero_at;
  char last_seen_at[MAX_TIME_TEXT];
  char ip_text[MAX_IP_TEXT];
  bool initialized;
};

struct previous_mac_entry {
  struct device_traffic_mac_key key;
  uint64_t upload_bytes;
  uint64_t download_bytes;
  char last_seen_at[MAX_TIME_TEXT];
};

struct previous_ip_entry {
  struct device_traffic_ip_key key;
  uint64_t upload_bytes;
  uint64_t download_bytes;
  char last_seen_at[MAX_TIME_TEXT];
  char ip_text[MAX_IP_TEXT];
};

struct runtime_state {
  char current_day[11];
  char previous_day[11];
  struct mac_runtime_entry mac_entries[DEVICE_TRAFFIC_MAX_MAC_ENTRIES];
  struct ip_runtime_entry ip_entries[DEVICE_TRAFFIC_MAX_IP_ENTRIES];
  struct previous_mac_entry previous_mac_entries[DEVICE_TRAFFIC_MAX_MAC_ENTRIES];
  struct previous_ip_entry previous_ip_entries[DEVICE_TRAFFIC_MAX_IP_ENTRIES];
  size_t mac_count;
  size_t ip_count;
  size_t previous_mac_count;
  size_t previous_ip_count;
};

struct string_builder {
  char *data;
  size_t len;
  size_t cap;
};

struct collector_runtime {
  struct app_config config;
  struct runtime_state state;
  struct bpf_object *object;
  int mac_map_fd;
  int ip_map_fd;
  int config_map_fd;
  int server_fd;
  int ifindex;
};

static int ensure_parent_directory(const char *path);
static void trim_line(char *line);

static void handle_signal(int sig)
{
  (void)sig;
  g_running = 0;
}

static void log_message(const char *level, const char *message)
{
  fprintf(stderr, "[device-traffic][%s] %s\n", level, message);
}

static int libbpf_log_callback(enum libbpf_print_level level, const char *fmt, va_list args)
{
  char buffer[512];
  const char *tag = "warn";

  if (level != LIBBPF_WARN)
    return 0;

  vsnprintf(buffer, sizeof(buffer), fmt, args);
  trim_line(buffer);
  if (buffer[0] != '\0')
    fprintf(stderr, "[device-traffic][libbpf][%s] %s\n", tag, buffer);
  return 0;
}

static void log_errno_detail(const char *context, int err)
{
  char buffer[256];
  const char *detail = "unknown";
  int code = err < 0 ? -err : err;

  if (code > 0)
    detail = strerror(code);
  snprintf(buffer, sizeof(buffer), "%s (err=%d: %s)", context, err, detail);
  log_message("error", buffer);
}

static int ensure_directory(const char *path, mode_t mode)
{
  if (mkdir(path, mode) == 0 || errno == EEXIST)
    return 0;
  return -errno;
}

static void format_mac(const uint8_t *mac, char *buffer, size_t size)
{
  snprintf(buffer, size, "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3],
           mac[4], mac[5]);
}

static int parse_mac(const char *text, uint8_t *mac)
{
  unsigned int values[6];

  if (sscanf(text, "%02x:%02x:%02x:%02x:%02x:%02x", &values[0], &values[1], &values[2],
             &values[3], &values[4], &values[5]) != 6)
    return -EINVAL;

  for (int i = 0; i < 6; i++)
    mac[i] = (uint8_t)values[i];
  return 0;
}

static void trim_line(char *line)
{
  size_t len = strlen(line);
  while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r' || line[len - 1] == ',' ||
                     line[len - 1] == ' ' || line[len - 1] == '\t')) {
    line[len - 1] = '\0';
    len--;
  }
}

static void today_string(char *buffer, size_t size)
{
  time_t now = time(NULL);
  struct tm tm_now;

  localtime_r(&now, &tm_now);
  strftime(buffer, size, "%Y-%m-%d", &tm_now);
}

static void iso_time_from_unix(time_t value, char *buffer, size_t size)
{
  struct tm tm_utc;

  gmtime_r(&value, &tm_utc);
  strftime(buffer, size, "%Y-%m-%dT%H:%M:%SZ", &tm_utc);
}

static void iso_time_now(char *buffer, size_t size)
{
  iso_time_from_unix(time(NULL), buffer, size);
}

static int sb_reserve(struct string_builder *sb, size_t needed)
{
  if (sb->len + needed + 1 <= sb->cap)
    return 0;

  size_t next_cap = sb->cap ? sb->cap : 1024;
  while (next_cap < sb->len + needed + 1)
    next_cap *= 2;

  char *next = realloc(sb->data, next_cap);
  if (!next)
    return -ENOMEM;

  sb->data = next;
  sb->cap = next_cap;
  return 0;
}

static int sb_append(struct string_builder *sb, const char *text)
{
  size_t len = strlen(text);
  int err = sb_reserve(sb, len);
  if (err)
    return err;

  memcpy(sb->data + sb->len, text, len);
  sb->len += len;
  sb->data[sb->len] = '\0';
  return 0;
}

static int sb_appendf(struct string_builder *sb, const char *fmt, ...)
{
  va_list args;
  va_list copy;
  int needed;

  va_start(args, fmt);
  va_copy(copy, args);
  needed = vsnprintf(NULL, 0, fmt, copy);
  va_end(copy);
  if (needed < 0) {
    va_end(args);
    return -EINVAL;
  }

  int err = sb_reserve(sb, (size_t)needed);
  if (err) {
    va_end(args);
    return err;
  }

  vsnprintf(sb->data + sb->len, sb->cap - sb->len, fmt, args);
  va_end(args);
  sb->len += (size_t)needed;
  return 0;
}

static int parse_ipv4_cidr(const char *text, struct device_traffic_v4_prefix *prefix)
{
  char buffer[64];
  char *slash;
  long prefix_len;
  struct in_addr addr;
  uint32_t mask;

  if (strlen(text) >= sizeof(buffer))
    return -EINVAL;

  strcpy(buffer, text);
  slash = strchr(buffer, '/');
  if (!slash)
    return -EINVAL;
  *slash = '\0';
  prefix_len = strtol(slash + 1, NULL, 10);
  if (prefix_len < 0 || prefix_len > 32)
    return -EINVAL;
  if (inet_pton(AF_INET, buffer, &addr) != 1)
    return -EINVAL;

  if (prefix_len == 0)
    mask = 0;
  else
    mask = htonl(0xffffffffu << (32 - prefix_len));

  prefix->mask = mask;
  prefix->network = addr.s_addr & mask;
  return 0;
}

static int parse_ipv6_prefix(const char *text, struct device_traffic_v6_prefix *prefix)
{
  char buffer[128];
  char *slash;
  long prefix_len;

  if (strlen(text) >= sizeof(buffer))
    return -EINVAL;

  strcpy(buffer, text);
  slash = strchr(buffer, '/');
  if (!slash)
    return -EINVAL;
  *slash = '\0';
  prefix_len = strtol(slash + 1, NULL, 10);
  if (prefix_len < 0 || prefix_len > 128)
    return -EINVAL;
  if (inet_pton(AF_INET6, buffer, prefix->addr) != 1)
    return -EINVAL;

  prefix->prefix_len = (uint32_t)prefix_len;
  return 0;
}

static void parse_csv_prefixes(const char *value, struct device_traffic_config *config)
{
  char *copy = NULL;
  char *token;
  char *saveptr = NULL;

  if (!value || !*value)
    return;

  copy = strdup(value);
  if (!copy)
    return;

  for (token = strtok_r(copy, ",", &saveptr); token; token = strtok_r(NULL, ",", &saveptr)) {
    while (*token == ' ' || *token == '\t')
      token++;
    if (strchr(token, ':')) {
      if (config->v6_count < DEVICE_TRAFFIC_MAX_V6_PREFIXES &&
          parse_ipv6_prefix(token, &config->v6[config->v6_count]) == 0)
        config->v6_count++;
      continue;
    }
    if (config->v4_count < DEVICE_TRAFFIC_MAX_V4_PREFIXES &&
        parse_ipv4_cidr(token, &config->v4[config->v4_count]) == 0)
      config->v4_count++;
  }

  free(copy);
}

static int load_config(struct app_config *config)
{
  const char *iface = getenv("DEVICE_TRAFFIC_LAN_INTERFACE");
  const char *socket_path = getenv("DEVICE_TRAFFIC_SOCKET_PATH");
  const char *state_path = getenv("DEVICE_TRAFFIC_STATE_PATH");
  const char *pin_root = getenv("DEVICE_TRAFFIC_BPF_PIN_ROOT");
  const char *poll_seconds = getenv("DEVICE_TRAFFIC_POLL_SECONDS");
  const char *flush_seconds = getenv("DEVICE_TRAFFIC_FLUSH_SECONDS");
  const char *hold_seconds = getenv("DEVICE_TRAFFIC_HOLD_SECONDS");
  const char *subnets_v4 = getenv("DEVICE_TRAFFIC_LAN_SUBNETS_V4");
  const char *prefixes_v6 = getenv("DEVICE_TRAFFIC_LAN_PREFIXES_V6");
  const char *bpf_object = getenv("DEVICE_TRAFFIC_BPF_OBJECT");
  char exe_path[PATH_MAX];
  ssize_t exe_len;
  char *slash;

  memset(config, 0, sizeof(*config));

  if (!iface || !*iface) {
    log_message("error", "DEVICE_TRAFFIC_LAN_INTERFACE is required");
    return -EINVAL;
  }

  snprintf(config->iface, sizeof(config->iface), "%s", iface);
  snprintf(config->socket_path, sizeof(config->socket_path), "%s",
           socket_path && *socket_path ? socket_path : "/run/orbi-monitor-core/device-traffic.sock");
  snprintf(config->state_path, sizeof(config->state_path), "%s",
           state_path && *state_path ? state_path
                                     : "/var/lib/orbi-monitor-core/device-traffic-state.json");
  snprintf(config->pin_root, sizeof(config->pin_root), "%s",
           pin_root && *pin_root ? pin_root : "/sys/fs/bpf/orbi-monitor-core/device-traffic");
  config->poll_seconds = poll_seconds && *poll_seconds ? atoi(poll_seconds) : 3;
  config->flush_seconds = flush_seconds && *flush_seconds ? atoi(flush_seconds) : 10;
  config->hold_seconds = hold_seconds && *hold_seconds ? atoi(hold_seconds) : 2;
  if (config->poll_seconds <= 0)
    config->poll_seconds = 3;
  if (config->flush_seconds <= 0)
    config->flush_seconds = 10;
  if (config->hold_seconds < 0)
    config->hold_seconds = 0;

  parse_csv_prefixes(subnets_v4 && *subnets_v4 ? subnets_v4 : "192.168.1.0/24",
                     &config->bpf_config);
  parse_csv_prefixes(prefixes_v6, &config->bpf_config);

  if (bpf_object && *bpf_object) {
    snprintf(config->bpf_object_path, sizeof(config->bpf_object_path), "%s", bpf_object);
    return 0;
  }

  exe_len = readlink("/proc/self/exe", exe_path, sizeof(exe_path) - 1);
  if (exe_len < 0)
    return -errno;
  exe_path[exe_len] = '\0';
  slash = strrchr(exe_path, '/');
  if (!slash)
    return -EINVAL;
  *slash = '\0';
  snprintf(config->bpf_object_path, sizeof(config->bpf_object_path), "%s/build/device_traffic.bpf.o",
           exe_path);
  return 0;
}

static bool mac_key_equal(const struct device_traffic_mac_key *left,
                          const struct device_traffic_mac_key *right)
{
  return memcmp(left->mac, right->mac, sizeof(left->mac)) == 0;
}

static bool ip_key_equal(const struct device_traffic_ip_key *left,
                         const struct device_traffic_ip_key *right)
{
  return left->family == right->family &&
         memcmp(left->addr, right->addr, sizeof(left->addr)) == 0;
}

static struct mac_runtime_entry *find_mac_entry(struct runtime_state *state,
                                                const struct device_traffic_mac_key *key)
{
  for (size_t i = 0; i < state->mac_count; i++) {
    if (mac_key_equal(&state->mac_entries[i].key, key))
      return &state->mac_entries[i];
  }
  return NULL;
}

static struct ip_runtime_entry *find_ip_entry(struct runtime_state *state,
                                              const struct device_traffic_ip_key *key)
{
  for (size_t i = 0; i < state->ip_count; i++) {
    if (ip_key_equal(&state->ip_entries[i].key, key))
      return &state->ip_entries[i];
  }
  return NULL;
}

static struct mac_runtime_entry *ensure_mac_entry(struct runtime_state *state,
                                                  const struct device_traffic_mac_key *key)
{
  struct mac_runtime_entry *entry = find_mac_entry(state, key);
  if (entry)
    return entry;
  if (state->mac_count >= DEVICE_TRAFFIC_MAX_MAC_ENTRIES)
    return NULL;
  entry = &state->mac_entries[state->mac_count++];
  memset(entry, 0, sizeof(*entry));
  memcpy(&entry->key, key, sizeof(*key));
  return entry;
}

static struct ip_runtime_entry *ensure_ip_entry(struct runtime_state *state,
                                                const struct device_traffic_ip_key *key)
{
  struct ip_runtime_entry *entry = find_ip_entry(state, key);
  if (entry)
    return entry;
  if (state->ip_count >= DEVICE_TRAFFIC_MAX_IP_ENTRIES)
    return NULL;
  entry = &state->ip_entries[state->ip_count++];
  memset(entry, 0, sizeof(*entry));
  memcpy(&entry->key, key, sizeof(*key));
  if (key->family == AF_INET)
    inet_ntop(AF_INET, key->addr, entry->ip_text, sizeof(entry->ip_text));
  else if (key->family == AF_INET6)
    inet_ntop(AF_INET6, key->addr, entry->ip_text, sizeof(entry->ip_text));
  return entry;
}

static uint64_t mac_upload_total(const struct device_traffic_mac_value *value)
{
  return value->upload_bytes_v4 + value->upload_bytes_v6;
}

static uint64_t mac_download_total(const struct device_traffic_mac_value *value)
{
  return value->download_bytes_v4 + value->download_bytes_v6;
}

static double apply_rate(double previous, double raw, time_t now, time_t *last_nonzero_at,
                         int hold_seconds, bool initialized)
{
  if (raw > 0.0) {
    *last_nonzero_at = now;
    if (!initialized)
      return raw;
    return previous * 0.7 + raw * 0.3;
  }
  if (hold_seconds > 0 && *last_nonzero_at > 0 && (now - *last_nonzero_at) <= hold_seconds)
    return previous;
  if (!initialized)
    return 0.0;
  return previous * 0.7;
}

static void update_mac_runtime(struct runtime_state *state, const struct device_traffic_mac_key *key,
                               const struct device_traffic_mac_value *value, double interval_seconds,
                               int hold_seconds, time_t now)
{
  struct mac_runtime_entry *entry = ensure_mac_entry(state, key);
  if (!entry)
    return;

  uint64_t current_upload = mac_upload_total(value);
  uint64_t current_download = mac_download_total(value);
  uint64_t previous_upload = mac_upload_total(&entry->prev_value);
  uint64_t previous_download = mac_download_total(&entry->prev_value);
  uint64_t delta_upload = current_upload >= previous_upload ? current_upload - previous_upload : current_upload;
  uint64_t delta_download =
      current_download >= previous_download ? current_download - previous_download : current_download;

  entry->day_upload_bytes += delta_upload;
  entry->day_download_bytes += delta_download;
  entry->upload_bps = apply_rate(entry->upload_bps, (delta_upload * 8.0) / interval_seconds, now,
                                 &entry->last_nonzero_at, hold_seconds, entry->initialized);
  entry->download_bps = apply_rate(entry->download_bps, (delta_download * 8.0) / interval_seconds, now,
                                   &entry->last_nonzero_at, hold_seconds, entry->initialized);
  iso_time_now(entry->last_seen_at, sizeof(entry->last_seen_at));
  entry->prev_value = *value;
  entry->initialized = true;
}

static void update_ip_runtime(struct runtime_state *state, const struct device_traffic_ip_key *key,
                              const struct device_traffic_ip_value *value, double interval_seconds,
                              int hold_seconds, time_t now)
{
  struct ip_runtime_entry *entry = ensure_ip_entry(state, key);
  if (!entry)
    return;

  uint64_t delta_upload =
      value->upload_bytes >= entry->prev_value.upload_bytes ? value->upload_bytes - entry->prev_value.upload_bytes
                                                            : value->upload_bytes;
  uint64_t delta_download =
      value->download_bytes >= entry->prev_value.download_bytes
          ? value->download_bytes - entry->prev_value.download_bytes
          : value->download_bytes;

  entry->day_upload_bytes += delta_upload;
  entry->day_download_bytes += delta_download;
  entry->upload_bps = apply_rate(entry->upload_bps, (delta_upload * 8.0) / interval_seconds, now,
                                 &entry->last_nonzero_at, hold_seconds, entry->initialized);
  entry->download_bps = apply_rate(entry->download_bps, (delta_download * 8.0) / interval_seconds, now,
                                   &entry->last_nonzero_at, hold_seconds, entry->initialized);
  memcpy(&entry->prev_value, value, sizeof(*value));
  iso_time_now(entry->last_seen_at, sizeof(entry->last_seen_at));
  entry->initialized = true;
}

static int read_mac_map_batch(int fd, struct device_traffic_mac_key *keys,
                              struct device_traffic_mac_value *values, size_t capacity)
{
  struct device_traffic_mac_key batch = {};
  size_t total = 0;
  void *in_batch = NULL;
  struct device_traffic_mac_key *out_batch = &batch;

  while (total < capacity) {
    __u32 count = (uint32_t)(capacity - total);
    errno = 0;
    int err = bpf_map_lookup_batch(fd, in_batch, out_batch, keys + total, values + total, &count, NULL);
    if (err == 0) {
      if (count == 0)
        return -EIO;
      total += count;
      in_batch = out_batch;
      continue;
    }
    if (errno == ENOENT) {
      total += count;
      return (int)total;
    }
    return -errno;
  }

  return (int)total;
}

static int read_ip_map_batch(int fd, struct device_traffic_ip_key *keys,
                             struct device_traffic_ip_value *values, size_t capacity)
{
  struct device_traffic_ip_key batch = {};
  size_t total = 0;
  void *in_batch = NULL;
  struct device_traffic_ip_key *out_batch = &batch;

  while (total < capacity) {
    __u32 count = (uint32_t)(capacity - total);
    errno = 0;
    int err = bpf_map_lookup_batch(fd, in_batch, out_batch, keys + total, values + total, &count, NULL);
    if (err == 0) {
      if (count == 0)
        return -EIO;
      total += count;
      in_batch = out_batch;
      continue;
    }
    if (errno == ENOENT) {
      total += count;
      return (int)total;
    }
    return -errno;
  }

  return (int)total;
}

static int read_mac_map_iter(int fd, struct device_traffic_mac_key *keys,
                             struct device_traffic_mac_value *values, size_t capacity)
{
  size_t total = 0;
  struct device_traffic_mac_key current = {};
  struct device_traffic_mac_key next = {};
  struct device_traffic_mac_key *cursor = NULL;

  while (total < capacity && bpf_map_get_next_key(fd, cursor, &next) == 0) {
    if (bpf_map_lookup_elem(fd, &next, &values[total]) == 0) {
      keys[total++] = next;
    }
    current = next;
    cursor = &current;
  }

  return (int)total;
}

static int read_ip_map_iter(int fd, struct device_traffic_ip_key *keys,
                            struct device_traffic_ip_value *values, size_t capacity)
{
  size_t total = 0;
  struct device_traffic_ip_key current = {};
  struct device_traffic_ip_key next = {};
  struct device_traffic_ip_key *cursor = NULL;

  while (total < capacity && bpf_map_get_next_key(fd, cursor, &next) == 0) {
    if (bpf_map_lookup_elem(fd, &next, &values[total]) == 0) {
      keys[total++] = next;
    }
    current = next;
    cursor = &current;
  }

  return (int)total;
}

static void snapshot_previous_day(struct runtime_state *state)
{
  state->previous_mac_count = 0;
  state->previous_ip_count = 0;

  for (size_t i = 0; i < state->mac_count && state->previous_mac_count < DEVICE_TRAFFIC_MAX_MAC_ENTRIES; i++) {
    if (!state->mac_entries[i].day_upload_bytes && !state->mac_entries[i].day_download_bytes)
      continue;
    struct previous_mac_entry *entry = &state->previous_mac_entries[state->previous_mac_count++];
    memset(entry, 0, sizeof(*entry));
    entry->key = state->mac_entries[i].key;
    entry->upload_bytes = state->mac_entries[i].day_upload_bytes;
    entry->download_bytes = state->mac_entries[i].day_download_bytes;
    snprintf(entry->last_seen_at, sizeof(entry->last_seen_at), "%s", state->mac_entries[i].last_seen_at);
  }

  for (size_t i = 0; i < state->ip_count && state->previous_ip_count < DEVICE_TRAFFIC_MAX_IP_ENTRIES; i++) {
    if (!state->ip_entries[i].day_upload_bytes && !state->ip_entries[i].day_download_bytes)
      continue;
    struct previous_ip_entry *entry = &state->previous_ip_entries[state->previous_ip_count++];
    memset(entry, 0, sizeof(*entry));
    entry->key = state->ip_entries[i].key;
    entry->upload_bytes = state->ip_entries[i].day_upload_bytes;
    entry->download_bytes = state->ip_entries[i].day_download_bytes;
    snprintf(entry->last_seen_at, sizeof(entry->last_seen_at), "%s", state->ip_entries[i].last_seen_at);
    snprintf(entry->ip_text, sizeof(entry->ip_text), "%s", state->ip_entries[i].ip_text);
  }
}

static void rollover_day(struct runtime_state *state, const char *today)
{
  if (state->current_day[0] == '\0') {
    snprintf(state->current_day, sizeof(state->current_day), "%s", today);
    return;
  }
  if (strcmp(state->current_day, today) == 0)
    return;

  snprintf(state->previous_day, sizeof(state->previous_day), "%s", state->current_day);
  snapshot_previous_day(state);
  snprintf(state->current_day, sizeof(state->current_day), "%s", today);

  for (size_t i = 0; i < state->mac_count; i++) {
    state->mac_entries[i].day_upload_bytes = 0;
    state->mac_entries[i].day_download_bytes = 0;
  }
  for (size_t i = 0; i < state->ip_count; i++) {
    state->ip_entries[i].day_upload_bytes = 0;
    state->ip_entries[i].day_download_bytes = 0;
  }
}

static void load_state_file(struct runtime_state *state, const char *path)
{
  FILE *file = fopen(path, "r");
  char line[512];
  enum {
    SECTION_NONE,
    SECTION_CURRENT_MAC,
    SECTION_CURRENT_IP,
    SECTION_PREVIOUS_MAC,
    SECTION_PREVIOUS_IP,
  } section = SECTION_NONE;
  char today[11];

  today_string(today, sizeof(today));
  if (!file) {
    snprintf(state->current_day, sizeof(state->current_day), "%s", today);
    return;
  }

  while (fgets(line, sizeof(line), file)) {
    trim_line(line);
    if (strstr(line, "\"current_day\"")) {
      sscanf(line, "\"current_day\": \"%10[^\"]\"", state->current_day);
      continue;
    }
    if (strstr(line, "\"previous_day\"")) {
      sscanf(line, "\"previous_day\": \"%10[^\"]\"", state->previous_day);
      continue;
    }
    if (strstr(line, "\"current_mac_items\"")) {
      section = SECTION_CURRENT_MAC;
      continue;
    }
    if (strstr(line, "\"current_ip_items\"")) {
      section = SECTION_CURRENT_IP;
      continue;
    }
    if (strstr(line, "\"previous_mac_items\"")) {
      section = SECTION_PREVIOUS_MAC;
      continue;
    }
    if (strstr(line, "\"previous_ip_items\"")) {
      section = SECTION_PREVIOUS_IP;
      continue;
    }
    if (line[0] == ']') {
      section = SECTION_NONE;
      continue;
    }
    if (line[0] != '{')
      continue;

    if (section == SECTION_CURRENT_MAC) {
      char mac_text[18] = {0};
      unsigned long long upload = 0;
      unsigned long long download = 0;
      char last_seen[MAX_TIME_TEXT] = {0};
      if (sscanf(line,
                 "{\"mac\":\"%17[^\"]\",\"upload_bytes\":%llu,\"download_bytes\":%llu,"
                 "\"last_seen_at\":\"%39[^\"]\"}",
                 mac_text, &upload, &download, last_seen) == 4) {
        struct device_traffic_mac_key key = {};
        if (parse_mac(mac_text, key.mac) == 0) {
          struct mac_runtime_entry *entry = ensure_mac_entry(state, &key);
          if (entry) {
            entry->day_upload_bytes = upload;
            entry->day_download_bytes = download;
            snprintf(entry->last_seen_at, sizeof(entry->last_seen_at), "%s", last_seen);
          }
        }
      }
      continue;
    }
    if (section == SECTION_CURRENT_IP) {
      char family_text[8] = {0};
      char ip_text[MAX_IP_TEXT] = {0};
      unsigned long long upload = 0;
      unsigned long long download = 0;
      char last_seen[MAX_TIME_TEXT] = {0};
      struct device_traffic_ip_key key = {};
      if (sscanf(line,
                 "{\"family\":\"%7[^\"]\",\"ip\":\"%45[^\"]\",\"upload_bytes\":%llu,"
                 "\"download_bytes\":%llu,\"last_seen_at\":\"%39[^\"]\"}",
                 family_text, ip_text, &upload, &download, last_seen) == 5) {
        if (strcmp(family_text, "ipv4") == 0) {
          key.family = AF_INET;
          if (inet_pton(AF_INET, ip_text, key.addr) != 1)
            continue;
        } else if (strcmp(family_text, "ipv6") == 0) {
          key.family = AF_INET6;
          if (inet_pton(AF_INET6, ip_text, key.addr) != 1)
            continue;
        } else {
          continue;
        }
        struct ip_runtime_entry *entry = ensure_ip_entry(state, &key);
        if (entry) {
          entry->day_upload_bytes = upload;
          entry->day_download_bytes = download;
          snprintf(entry->last_seen_at, sizeof(entry->last_seen_at), "%s", last_seen);
          snprintf(entry->ip_text, sizeof(entry->ip_text), "%s", ip_text);
        }
      }
      continue;
    }
    if (section == SECTION_PREVIOUS_MAC) {
      char mac_text[18] = {0};
      unsigned long long upload = 0;
      unsigned long long download = 0;
      char last_seen[MAX_TIME_TEXT] = {0};
      if (state->previous_mac_count >= DEVICE_TRAFFIC_MAX_MAC_ENTRIES)
        continue;
      if (sscanf(line,
                 "{\"mac\":\"%17[^\"]\",\"upload_bytes\":%llu,\"download_bytes\":%llu,"
                 "\"last_seen_at\":\"%39[^\"]\"}",
                 mac_text, &upload, &download, last_seen) == 4) {
        struct previous_mac_entry *entry = &state->previous_mac_entries[state->previous_mac_count];
        if (parse_mac(mac_text, entry->key.mac) == 0) {
          entry->upload_bytes = upload;
          entry->download_bytes = download;
          snprintf(entry->last_seen_at, sizeof(entry->last_seen_at), "%s", last_seen);
          state->previous_mac_count++;
        }
      }
      continue;
    }
    if (section == SECTION_PREVIOUS_IP) {
      char family_text[8] = {0};
      char ip_text[MAX_IP_TEXT] = {0};
      unsigned long long upload = 0;
      unsigned long long download = 0;
      char last_seen[MAX_TIME_TEXT] = {0};
      if (state->previous_ip_count >= DEVICE_TRAFFIC_MAX_IP_ENTRIES)
        continue;
      if (sscanf(line,
                 "{\"family\":\"%7[^\"]\",\"ip\":\"%45[^\"]\",\"upload_bytes\":%llu,"
                 "\"download_bytes\":%llu,\"last_seen_at\":\"%39[^\"]\"}",
                 family_text, ip_text, &upload, &download, last_seen) == 5) {
        struct previous_ip_entry *entry = &state->previous_ip_entries[state->previous_ip_count];
        memset(entry, 0, sizeof(*entry));
        if (strcmp(family_text, "ipv4") == 0) {
          entry->key.family = AF_INET;
          if (inet_pton(AF_INET, ip_text, entry->key.addr) != 1)
            continue;
        } else if (strcmp(family_text, "ipv6") == 0) {
          entry->key.family = AF_INET6;
          if (inet_pton(AF_INET6, ip_text, entry->key.addr) != 1)
            continue;
        } else {
          continue;
        }
        entry->upload_bytes = upload;
        entry->download_bytes = download;
        snprintf(entry->last_seen_at, sizeof(entry->last_seen_at), "%s", last_seen);
        snprintf(entry->ip_text, sizeof(entry->ip_text), "%s", ip_text);
        state->previous_ip_count++;
      }
    }
  }

  fclose(file);

  if (state->current_day[0] == '\0')
    snprintf(state->current_day, sizeof(state->current_day), "%s", today);

  if (strcmp(state->current_day, today) != 0) {
    if (state->current_day[0] != '\0') {
      snprintf(state->previous_day, sizeof(state->previous_day), "%s", state->current_day);
      snapshot_previous_day(state);
    }
    snprintf(state->current_day, sizeof(state->current_day), "%s", today);
    for (size_t i = 0; i < state->mac_count; i++) {
      state->mac_entries[i].day_upload_bytes = 0;
      state->mac_entries[i].day_download_bytes = 0;
    }
    for (size_t i = 0; i < state->ip_count; i++) {
      state->ip_entries[i].day_upload_bytes = 0;
      state->ip_entries[i].day_download_bytes = 0;
    }
  }
}

static int write_state_file(const struct runtime_state *state, const char *path)
{
  char tmp_path[PATH_MAX];
  FILE *file;
  char mac_text[18];

  if (ensure_parent_directory(path) != 0)
    return -errno;

  snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", path);
  file = fopen(tmp_path, "w");
  if (!file)
    return -errno;

  fprintf(file, "{\n");
  fprintf(file, "  \"current_day\": \"%s\",\n", state->current_day);
  fprintf(file, "  \"previous_day\": \"%s\",\n", state->previous_day);
  fprintf(file, "  \"current_mac_items\": [\n");
  bool first = true;
  for (size_t i = 0; i < state->mac_count; i++) {
    const struct mac_runtime_entry *entry = &state->mac_entries[i];
    if (!entry->day_upload_bytes && !entry->day_download_bytes)
      continue;
    format_mac(entry->key.mac, mac_text, sizeof(mac_text));
    fprintf(file,
            "    %s{\"mac\":\"%s\",\"upload_bytes\":%" PRIu64 ",\"download_bytes\":%" PRIu64
            ",\"last_seen_at\":\"%s\"}\n",
            first ? "" : ",", mac_text, entry->day_upload_bytes, entry->day_download_bytes,
            entry->last_seen_at);
    first = false;
  }
  fprintf(file, "  ],\n");
  fprintf(file, "  \"current_ip_items\": [\n");
  first = true;
  for (size_t i = 0; i < state->ip_count; i++) {
    const struct ip_runtime_entry *entry = &state->ip_entries[i];
    const char *family = entry->key.family == AF_INET ? "ipv4" : "ipv6";
    if (!entry->day_upload_bytes && !entry->day_download_bytes)
      continue;
    fprintf(file,
            "    %s{\"family\":\"%s\",\"ip\":\"%s\",\"upload_bytes\":%" PRIu64
            ",\"download_bytes\":%" PRIu64 ",\"last_seen_at\":\"%s\"}\n",
            first ? "" : ",", family, entry->ip_text, entry->day_upload_bytes,
            entry->day_download_bytes, entry->last_seen_at);
    first = false;
  }
  fprintf(file, "  ],\n");
  fprintf(file, "  \"previous_mac_items\": [\n");
  first = true;
  for (size_t i = 0; i < state->previous_mac_count; i++) {
    const struct previous_mac_entry *entry = &state->previous_mac_entries[i];
    format_mac(entry->key.mac, mac_text, sizeof(mac_text));
    fprintf(file,
            "    %s{\"mac\":\"%s\",\"upload_bytes\":%" PRIu64 ",\"download_bytes\":%" PRIu64
            ",\"last_seen_at\":\"%s\"}\n",
            first ? "" : ",", mac_text, entry->upload_bytes, entry->download_bytes,
            entry->last_seen_at);
    first = false;
  }
  fprintf(file, "  ],\n");
  fprintf(file, "  \"previous_ip_items\": [\n");
  first = true;
  for (size_t i = 0; i < state->previous_ip_count; i++) {
    const struct previous_ip_entry *entry = &state->previous_ip_entries[i];
    const char *family = entry->key.family == AF_INET ? "ipv4" : "ipv6";
    fprintf(file,
            "    %s{\"family\":\"%s\",\"ip\":\"%s\",\"upload_bytes\":%" PRIu64
            ",\"download_bytes\":%" PRIu64 ",\"last_seen_at\":\"%s\"}\n",
            first ? "" : ",", family, entry->ip_text, entry->upload_bytes,
            entry->download_bytes, entry->last_seen_at);
    first = false;
  }
  fprintf(file, "  ]\n");
  fprintf(file, "}\n");

  fclose(file);
  if (rename(tmp_path, path) != 0)
    return -errno;
  return 0;
}

static int ensure_parent_directory(const char *path)
{
  char buffer[PATH_MAX];
  char *slash;

  snprintf(buffer, sizeof(buffer), "%s", path);
  slash = strrchr(buffer, '/');
  if (!slash)
    return 0;
  *slash = '\0';
  if (!*buffer)
    return 0;
  return ensure_directory(buffer, 0755);
}

static int setup_server_socket(struct collector_runtime *collector)
{
  struct sockaddr_un addr = {};
  int fd;

  if (ensure_parent_directory(collector->config.socket_path) != 0)
    return -errno;

  unlink(collector->config.socket_path);
  fd = socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0)
    return -errno;

  addr.sun_family = AF_UNIX;
  snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", collector->config.socket_path);

  if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
    close(fd);
    return -errno;
  }
  if (chmod(collector->config.socket_path, 0660) != 0) {
    close(fd);
    unlink(collector->config.socket_path);
    return -errno;
  }
  if (listen(fd, SOCKET_BACKLOG) != 0) {
    close(fd);
    unlink(collector->config.socket_path);
    return -errno;
  }

  collector->server_fd = fd;
  return 0;
}

static int pin_map(struct bpf_object *object, const char *map_name, const char *pin_root)
{
  struct bpf_map *map = bpf_object__find_map_by_name(object, map_name);
  char path[PATH_MAX];

  if (!map)
    return -ENOENT;
  snprintf(path, sizeof(path), "%s/%s", pin_root, map_name);
  unlink(path);
  return bpf_map__pin(map, path);
}

static int attach_tc_program(struct bpf_program *program, int ifindex, enum bpf_tc_attach_point point,
                             __u32 handle, __u32 priority)
{
  struct bpf_tc_hook hook = {
      .sz = sizeof(hook),
      .ifindex = ifindex,
      .attach_point = point,
  };
  struct bpf_tc_opts detach_opts = {
      .sz = sizeof(detach_opts),
      .handle = handle,
      .priority = priority,
  };
  struct bpf_tc_opts opts = {
      .sz = sizeof(opts),
      .prog_fd = bpf_program__fd(program),
      .handle = handle,
      .priority = priority,
  };
  int err;

  err = bpf_tc_hook_create(&hook);
  if (err && err != -EEXIST)
    return err;

  bpf_tc_detach(&hook, &detach_opts);
  err = bpf_tc_attach(&hook, &opts);
  if (err == -EEXIST) {
    bpf_tc_detach(&hook, &detach_opts);
    err = bpf_tc_attach(&hook, &opts);
  }
  return err;
}

static int load_bpf(struct collector_runtime *collector)
{
  struct bpf_object_open_opts opts = {
      .sz = sizeof(struct bpf_object_open_opts),
  };
  struct bpf_map *config_map;
  struct bpf_map *mac_map;
  struct bpf_map *ip_map;
  struct bpf_program *ingress;
  struct bpf_program *egress;
  __u32 key = 0;
  int err;

  collector->ifindex = if_nametoindex(collector->config.iface);
  if (!collector->ifindex) {
    log_errno_detail("if_nametoindex failed", -errno);
    return -errno;
  }

  err = ensure_directory("/sys/fs/bpf/orbi-monitor-core", 0755);
  if (err != 0) {
    log_errno_detail("failed to ensure /sys/fs/bpf/orbi-monitor-core", err);
    return err;
  }
  err = ensure_directory(collector->config.pin_root, 0755);
  if (err != 0) {
    log_errno_detail("failed to ensure pin root", err);
    return err;
  }

  libbpf_set_strict_mode(LIBBPF_STRICT_ALL);
  libbpf_set_print(libbpf_log_callback);
  opts.pin_root_path = collector->config.pin_root;

  collector->object = bpf_object__open_file(collector->config.bpf_object_path, &opts);
  if (!collector->object) {
    char buffer[PATH_MAX + 96];
    snprintf(buffer, sizeof(buffer), "bpf_object__open_file returned NULL for %s",
             collector->config.bpf_object_path);
    log_message("error", buffer);
    return -EINVAL;
  }
  err = (int)libbpf_get_error(collector->object);
  if (err) {
    collector->object = NULL;
    log_errno_detail("bpf_object__open_file failed", err);
    return err;
  }

  ingress = bpf_object__find_program_by_name(collector->object, "device_traffic_ingress");
  egress = bpf_object__find_program_by_name(collector->object, "device_traffic_egress");
  if (!ingress || !egress) {
    log_message("error", "failed to find ingress/egress programs before load");
    return -ENOENT;
  }
  bpf_program__set_type(ingress, BPF_PROG_TYPE_SCHED_CLS);
  bpf_program__set_type(egress, BPF_PROG_TYPE_SCHED_CLS);

  err = bpf_object__load(collector->object);
  if (err) {
    log_errno_detail("bpf_object__load failed", err);
    return err;
  }

  config_map = bpf_object__find_map_by_name(collector->object, "traffic_config");
  mac_map = bpf_object__find_map_by_name(collector->object, "device_stats");
  ip_map = bpf_object__find_map_by_name(collector->object, "ip_stats");
  if (!ingress || !egress || !config_map || !mac_map || !ip_map) {
    log_message("error", "failed to find ingress/egress programs or pinned maps");
    return -ENOENT;
  }

  collector->config_map_fd = bpf_map__fd(config_map);
  collector->mac_map_fd = bpf_map__fd(mac_map);
  collector->ip_map_fd = bpf_map__fd(ip_map);

  err = bpf_map_update_elem(collector->config_map_fd, &key, &collector->config.bpf_config, BPF_ANY);
  if (err) {
    log_errno_detail("bpf_map_update_elem(traffic_config) failed", -errno);
    return -errno;
  }

  err = pin_map(collector->object, "traffic_config", collector->config.pin_root);
  if (err) {
    log_errno_detail("failed to pin traffic_config", err);
    return err;
  }
  err = pin_map(collector->object, "device_stats", collector->config.pin_root);
  if (err) {
    log_errno_detail("failed to pin device_stats", err);
    return err;
  }
  err = pin_map(collector->object, "ip_stats", collector->config.pin_root);
  if (err) {
    log_errno_detail("failed to pin ip_stats", err);
    return err;
  }

  err = attach_tc_program(ingress, collector->ifindex, BPF_TC_INGRESS, 1, 1);
  if (err) {
    log_errno_detail("failed to attach ingress tc program", err);
    return err;
  }
  err = attach_tc_program(egress, collector->ifindex, BPF_TC_EGRESS, 2, 1);
  if (err) {
    log_errno_detail("failed to attach egress tc program", err);
    return err;
  }

  return 0;
}

static void detach_tc_program(int ifindex, enum bpf_tc_attach_point point, __u32 handle, __u32 priority)
{
  struct bpf_tc_hook hook = {
      .sz = sizeof(hook),
      .ifindex = ifindex,
      .attach_point = point,
  };
  struct bpf_tc_opts opts = {
      .sz = sizeof(opts),
      .handle = handle,
      .priority = priority,
  };

  bpf_tc_detach(&hook, &opts);
}

static void cleanup_bpf(struct collector_runtime *collector)
{
  if (collector->ifindex > 0) {
    detach_tc_program(collector->ifindex, BPF_TC_INGRESS, 1, 1);
    detach_tc_program(collector->ifindex, BPF_TC_EGRESS, 2, 1);
  }
  if (collector->object) {
    bpf_object__close(collector->object);
    collector->object = NULL;
  }
}

static int poll_maps(struct collector_runtime *collector)
{
  struct device_traffic_mac_key mac_keys[DEVICE_TRAFFIC_MAX_MAC_ENTRIES];
  struct device_traffic_mac_value mac_values[DEVICE_TRAFFIC_MAX_MAC_ENTRIES];
  struct device_traffic_ip_key ip_keys[DEVICE_TRAFFIC_MAX_IP_ENTRIES];
  struct device_traffic_ip_value ip_values[DEVICE_TRAFFIC_MAX_IP_ENTRIES];
  int mac_count;
  int ip_count;
  double interval_seconds = (double)collector->config.poll_seconds;
  char today[11];
  time_t now = time(NULL);

  today_string(today, sizeof(today));
  rollover_day(&collector->state, today);

  mac_count = read_mac_map_batch(collector->mac_map_fd, mac_keys, mac_values, DEVICE_TRAFFIC_MAX_MAC_ENTRIES);
  if (mac_count < 0)
    mac_count = read_mac_map_iter(collector->mac_map_fd, mac_keys, mac_values, DEVICE_TRAFFIC_MAX_MAC_ENTRIES);

  ip_count = read_ip_map_batch(collector->ip_map_fd, ip_keys, ip_values, DEVICE_TRAFFIC_MAX_IP_ENTRIES);
  if (ip_count < 0)
    ip_count = read_ip_map_iter(collector->ip_map_fd, ip_keys, ip_values, DEVICE_TRAFFIC_MAX_IP_ENTRIES);

  for (int i = 0; i < mac_count; i++)
    update_mac_runtime(&collector->state, &mac_keys[i], &mac_values[i], interval_seconds,
                       collector->config.hold_seconds, now);

  for (int i = 0; i < ip_count; i++)
    update_ip_runtime(&collector->state, &ip_keys[i], &ip_values[i], interval_seconds,
                      collector->config.hold_seconds, now);

  return 0;
}

static int build_snapshot_json(struct collector_runtime *collector, struct string_builder *sb)
{
  char checked_at[MAX_TIME_TEXT];
  char mac_text[18];

  iso_time_now(checked_at, sizeof(checked_at));
  sb->len = 0;
  if (sb->data)
    sb->data[0] = '\0';

  int err = sb_appendf(sb, "{\"checked_at\":\"%s\",\"poll_interval_seconds\":%d,\"mac_items\":[",
                       checked_at, collector->config.poll_seconds);
  if (err)
    return err;

  bool first = true;
  for (size_t i = 0; i < collector->state.mac_count; i++) {
    const struct mac_runtime_entry *entry = &collector->state.mac_entries[i];
    if (!entry->day_upload_bytes && !entry->day_download_bytes && entry->upload_bps < 0.01 &&
        entry->download_bps < 0.01)
      continue;
    format_mac(entry->key.mac, mac_text, sizeof(mac_text));
    err = sb_appendf(
        sb,
        "%s{\"mac\":\"%s\",\"download_bps\":%.2f,\"upload_bps\":%.2f,"
        "\"download_bytes_today\":%" PRIu64 ",\"upload_bytes_today\":%" PRIu64
        ",\"total_bytes_today\":%" PRIu64 ",\"last_seen_at\":\"%s\",\"active\":%s}",
        first ? "" : ",", mac_text, entry->download_bps, entry->upload_bps,
        entry->day_download_bytes, entry->day_upload_bytes,
        entry->day_download_bytes + entry->day_upload_bytes, entry->last_seen_at,
        (entry->download_bps > 0.01 || entry->upload_bps > 0.01) ? "true" : "false");
    if (err)
      return err;
    first = false;
  }

  err = sb_append(sb, "],\"ip_items\":[");
  if (err)
    return err;

  first = true;
  for (size_t i = 0; i < collector->state.ip_count; i++) {
    const struct ip_runtime_entry *entry = &collector->state.ip_entries[i];
    char observed_mac[18];
    const char *family = entry->key.family == AF_INET ? "ipv4" : "ipv6";
    if (!entry->day_upload_bytes && !entry->day_download_bytes && entry->upload_bps < 0.01 &&
        entry->download_bps < 0.01)
      continue;
    format_mac(entry->prev_value.observed_mac, observed_mac, sizeof(observed_mac));
    err = sb_appendf(
        sb,
        "%s{\"family\":\"%s\",\"ip\":\"%s\",\"observed_mac\":\"%s\","
        "\"download_bps\":%.2f,\"upload_bps\":%.2f,\"download_bytes_today\":%" PRIu64
        ",\"upload_bytes_today\":%" PRIu64 ",\"total_bytes_today\":%" PRIu64
        ",\"last_seen_at\":\"%s\",\"active\":%s}",
        first ? "" : ",", family, entry->ip_text, observed_mac, entry->download_bps,
        entry->upload_bps, entry->day_download_bytes, entry->day_upload_bytes,
        entry->day_download_bytes + entry->day_upload_bytes, entry->last_seen_at,
        (entry->download_bps > 0.01 || entry->upload_bps > 0.01) ? "true" : "false");
    if (err)
      return err;
    first = false;
  }

  return sb_append(sb, "]}");
}

static int serve_client(struct collector_runtime *collector)
{
  int client_fd = accept(collector->server_fd, NULL, NULL);
  struct string_builder sb = {};
  int err = 0;
  size_t sent = 0;

  if (client_fd < 0)
    return -errno;

  err = build_snapshot_json(collector, &sb);
  if (!err && sb.data) {
    while (sent < sb.len) {
      ssize_t written = send(client_fd, sb.data + sent, sb.len - sent, 0);
      if (written < 0) {
        if (errno == EINTR)
          continue;
        err = -errno;
        break;
      }
      if (written == 0)
        break;
      sent += (size_t)written;
    }
  }

  close(client_fd);
  free(sb.data);
  return err;
}

int main(void)
{
  struct collector_runtime collector = {};
  struct pollfd fds[1];
  time_t next_poll_at;
  time_t next_flush_at;
  char today[11];

  collector.server_fd = -1;
  signal(SIGINT, handle_signal);
  signal(SIGTERM, handle_signal);

  if (load_config(&collector.config) != 0)
    return 1;

  memset(&collector.state, 0, sizeof(collector.state));
  load_state_file(&collector.state, collector.config.state_path);
  today_string(today, sizeof(today));
  rollover_day(&collector.state, today);

  if (setup_server_socket(&collector) != 0) {
    log_message("error", "failed to set up unix socket");
    return 1;
  }

  if (load_bpf(&collector) != 0) {
    log_message("error", "failed to load tc eBPF programs");
    close(collector.server_fd);
    unlink(collector.config.socket_path);
    return 1;
  }

  poll_maps(&collector);
  write_state_file(&collector.state, collector.config.state_path);

  next_poll_at = time(NULL) + collector.config.poll_seconds;
  next_flush_at = time(NULL) + collector.config.flush_seconds;
  fds[0].fd = collector.server_fd;
  fds[0].events = POLLIN;

  while (g_running) {
    time_t now = time(NULL);
    int timeout_ms = 500;

    if (next_poll_at > now || next_flush_at > now) {
      time_t next_event = next_poll_at < next_flush_at ? next_poll_at : next_flush_at;
      long delta_ms = (long)(next_event - now) * 1000L;
      if (delta_ms < 0)
        delta_ms = 0;
      if (delta_ms < timeout_ms)
        timeout_ms = (int)delta_ms;
    } else {
      timeout_ms = 0;
    }

    int ready = poll(fds, 1, timeout_ms);
    if (ready > 0 && (fds[0].revents & POLLIN))
      serve_client(&collector);

    now = time(NULL);
    if (now >= next_poll_at) {
      poll_maps(&collector);
      next_poll_at = now + collector.config.poll_seconds;
    }
    if (now >= next_flush_at) {
      write_state_file(&collector.state, collector.config.state_path);
      next_flush_at = now + collector.config.flush_seconds;
    }
  }

  write_state_file(&collector.state, collector.config.state_path);
  cleanup_bpf(&collector);
  if (collector.server_fd >= 0)
    close(collector.server_fd);
  unlink(collector.config.socket_path);
  return 0;
}
