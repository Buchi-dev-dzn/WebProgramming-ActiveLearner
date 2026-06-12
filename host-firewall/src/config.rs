use std::fmt;

use host_firewall_common::{FirewallConfig, MAX_PORTS};

#[derive(Debug)]
pub enum FirewallConfigError {
    TooManyPorts(usize),
}

impl fmt::Display for FirewallConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FirewallConfigError::TooManyPorts(len) => {
                write!(f, "too many TCP ports for XDP backend: {len} > {MAX_PORTS}")
            }
        }
    }
}

pub fn validate_table_name(input: &str) -> Result<(), String> {
    if input.is_empty() {
        return Err("table name cannot be empty".to_string());
    }

    if input
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        Ok(())
    } else {
        Err(format!("invalid table name: {input}"))
    }
}

pub fn parse_ports(raw: &str) -> Result<Vec<u16>, String> {
    let mut ports = Vec::new();

    for part in raw.split(',') {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }

        ports.push(
            trimmed
                .parse::<u16>()
                .map_err(|_| format!("invalid port: {trimmed}"))?,
        );
    }

    if ports.is_empty() {
        return Err("at least one port is required".to_string());
    }

    Ok(ports)
}

pub fn join_ports(ports: &[u16]) -> String {
    ports
        .iter()
        .map(|port| port.to_string())
        .collect::<Vec<_>>()
        .join(", ")
}

pub fn build_firewall_config(ports: &[u16]) -> Result<FirewallConfig, FirewallConfigError> {
    if ports.len() > MAX_PORTS {
        return Err(FirewallConfigError::TooManyPorts(ports.len()));
    }

    let mut config = FirewallConfig::default();
    config.allowed_tcp_ports_len = ports.len() as u32;
    for (index, port) in ports.iter().copied().enumerate() {
        config.allowed_tcp_ports[index] = port;
    }

    Ok(config)
}
