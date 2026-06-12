use crate::XdpMode;

pub const TABLE_NAME: &str = "codex_host_filter";
pub const ALLOWED_TCP_PORTS: &[u16] = &[22, 80, 443];

pub const XDP_IFACE: Option<&str> = Some("eth0");
pub const XDP_OBJECT: &str = "./dist/host-firewall-xdp.o";
pub const XDP_PIN_PATH: &str = "/sys/fs/bpf/codex_host_filter/host_firewall_xdp";
pub const XDP_MODE: XdpMode = XdpMode::Native;

pub fn allowed_tcp_ports_csv() -> String {
    ALLOWED_TCP_PORTS
        .iter()
        .map(|port| port.to_string())
        .collect::<Vec<_>>()
        .join(",")
}
