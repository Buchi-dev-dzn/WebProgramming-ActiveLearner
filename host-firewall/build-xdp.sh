#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm \
  -v "$workspace_root":/workspace \
  -w /workspace/host-firewall-ebpf \
  ubuntu:24.04 \
  bash -lc '
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates build-essential pkg-config libelf-dev
    curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable
    . "$HOME/.cargo/env"
    rustup toolchain install nightly --component rust-src
    rustup target add bpfel-unknown-none --toolchain nightly
    cargo +nightly install bpf-linker
    cargo +nightly build --release
    mkdir -p /workspace/host-firewall/dist
    cp /workspace/host-firewall-ebpf/target/bpfel-unknown-none/release/host-firewall-xdp /workspace/host-firewall/dist/host-firewall-xdp.o
  '

echo "built XDP object: $workspace_root/host-firewall/dist/host-firewall-xdp.o"
