CLANG ?= clang
CC ?= cc
PKG_CONFIG ?= pkg-config

OUTPUT_DIR := build
BPF_OBJ := $(OUTPUT_DIR)/device_traffic.bpf.o
COLLECTOR_BIN := device_traffic

UNAME_M := $(shell uname -m)
DEB_HOST_MULTIARCH := $(shell dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo $(UNAME_M)-linux-gnu)
ifeq ($(UNAME_M),x86_64)
TARGET_ARCH := x86
else ifeq ($(UNAME_M),aarch64)
TARGET_ARCH := arm64
else ifeq ($(UNAME_M),arm64)
TARGET_ARCH := arm64
else
TARGET_ARCH := $(UNAME_M)
endif

BPF_CFLAGS := -O2 -g -target bpf -D__TARGET_ARCH_$(TARGET_ARCH) -Wall -Wextra -I/usr/include/$(DEB_HOST_MULTIARCH)
USER_CFLAGS := -O2 -g -Wall -Wextra -std=c11 $(shell $(PKG_CONFIG) --cflags libbpf)
USER_LDFLAGS := $(shell $(PKG_CONFIG) --libs libbpf) -lelf -lz

.PHONY: all clean

all: $(COLLECTOR_BIN) $(BPF_OBJ)

$(OUTPUT_DIR):
	mkdir -p $(OUTPUT_DIR)

$(BPF_OBJ): device_traffic.bpf.c device_traffic.h | $(OUTPUT_DIR)
	$(CLANG) $(BPF_CFLAGS) -c device_traffic.bpf.c -o $(BPF_OBJ)

$(COLLECTOR_BIN): device_traffic.c device_traffic.h
	$(CC) $(USER_CFLAGS) device_traffic.c -o $(COLLECTOR_BIN) $(USER_LDFLAGS)

clean:
	rm -rf $(OUTPUT_DIR) $(COLLECTOR_BIN)
