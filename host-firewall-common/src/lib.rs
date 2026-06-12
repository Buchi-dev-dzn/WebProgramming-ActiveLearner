#![no_std]

pub const MAX_PORTS: usize = 32;

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct FirewallConfig {
    pub allowed_tcp_ports: [u16; MAX_PORTS],
    pub allowed_tcp_ports_len: u32,
}

impl FirewallConfig {
    pub const fn empty() -> Self {
        Self {
            allowed_tcp_ports: [0; MAX_PORTS],
            allowed_tcp_ports_len: 0,
        }
    }
}

impl Default for FirewallConfig {
    fn default() -> Self {
        Self::empty()
    }
}
