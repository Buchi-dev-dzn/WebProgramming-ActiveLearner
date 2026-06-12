use std::process::{Command, Stdio};

use crate::config::join_ports;

pub fn build_rules(table_name: &str, allowed_tcp_ports: &[u16]) -> String {
    let mut lines = Vec::new();

    lines.push(format!("table inet {table_name} {{"));
    lines.push("  chain input {".to_string());
    lines.push("    type filter hook input priority 0;".to_string());
    lines.push("    policy drop;".to_string());
    lines.push(String::new());
    lines.push("    iif \"lo\" accept".to_string());
    lines.push("    ct state invalid drop".to_string());
    lines.push("    ct state established,related accept".to_string());
    lines.push(String::new());
    lines.push("    # Port whitelist packet filter".to_string());
    lines.push(format!(
        "    tcp dport {{ {} }} accept",
        join_ports(allowed_tcp_ports)
    ));
    lines.push("    ip protocol icmp accept".to_string());
    lines.push("    ip6 nexthdr ipv6-icmp accept".to_string());
    lines.push("  }".to_string());
    lines.push("}".to_string());

    lines.join("\n")
}

pub fn apply_rules(table_name: &str, rules: &str) -> Result<(), String> {
    let mut delete_cmd = Command::new("nft")
        .args(["delete", "table", "inet", table_name])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("failed to start cleanup command: {err}"))?;
    let _ = delete_cmd.wait();

    let mut child = Command::new("nft")
        .args(["-f", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|err| format!("failed to start nft: {err}"))?;

    {
        use std::io::Write;
        let stdin = child
            .stdin
            .as_mut()
            .ok_or_else(|| "failed to open stdin for nft".to_string())?;
        stdin
            .write_all(rules.as_bytes())
            .map_err(|err| format!("failed to write rules to nft stdin: {err}"))?;
    }

    let status = child
        .wait()
        .map_err(|err| format!("failed waiting for nft: {err}"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!("nft exited with status {status}"))
    }
}
