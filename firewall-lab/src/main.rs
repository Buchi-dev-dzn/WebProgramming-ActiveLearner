use std::env;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::process;
use std::time::{Duration, Instant};

struct ProbeResult {
    port: u16,
    outcome: &'static str,
    detail: String,
    elapsed_ms: u128,
}

fn parse_args() -> Result<(String, Vec<u16>, u64), String> {
    let mut target = None;
    let mut ports = None;
    let mut timeout_ms = 1500_u64;

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--target" => target = args.next(),
            "--ports" => ports = args.next(),
            "--timeout-ms" => {
                let raw = args
                    .next()
                    .ok_or_else(|| "missing value for --timeout-ms".to_string())?;
                timeout_ms = raw
                    .parse::<u64>()
                    .map_err(|_| format!("invalid timeout value: {raw}"))?;
            }
            "--help" | "-h" => {
                print_help();
                process::exit(0);
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }

    let target = target.ok_or_else(|| "missing --target".to_string())?;
    let ports = ports.ok_or_else(|| "missing --ports".to_string())?;
    let ports = parse_ports(&ports)?;

    Ok((target, ports, timeout_ms))
}

fn parse_ports(raw: &str) -> Result<Vec<u16>, String> {
    let mut ports = Vec::new();

    for part in raw.split(',') {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }

        let port = trimmed
            .parse::<u16>()
            .map_err(|_| format!("invalid port: {trimmed}"))?;
        ports.push(port);
    }

    if ports.is_empty() {
        return Err("no ports were provided".to_string());
    }

    Ok(ports)
}

fn resolve_addr(target: &str, port: u16) -> Result<SocketAddr, String> {
    (target, port)
        .to_socket_addrs()
        .map_err(|err| format!("failed to resolve {target}:{port}: {err}"))?
        .next()
        .ok_or_else(|| format!("no socket address resolved for {target}:{port}"))
}

fn probe_port(target: &str, port: u16, timeout_ms: u64) -> Result<ProbeResult, String> {
    let addr = resolve_addr(target, port)?;
    let timeout = Duration::from_millis(timeout_ms);
    let started = Instant::now();

    match TcpStream::connect_timeout(&addr, timeout) {
        Ok(stream) => {
            drop(stream);
            Ok(ProbeResult {
                port,
                outcome: "allowed",
                detail: "tcp handshake completed".to_string(),
                elapsed_ms: started.elapsed().as_millis(),
            })
        }
        Err(err) if err.kind() == std::io::ErrorKind::TimedOut => Ok(ProbeResult {
            port,
            outcome: "blocked_or_dropped",
            detail: "connect timeout; packet likely filtered or silently dropped".to_string(),
            elapsed_ms: started.elapsed().as_millis(),
        }),
        Err(err) if err.kind() == std::io::ErrorKind::ConnectionRefused => Ok(ProbeResult {
            port,
            outcome: "reachable_but_no_listener",
            detail: "host replied, but nothing is listening on this port".to_string(),
            elapsed_ms: started.elapsed().as_millis(),
        }),
        Err(err) => Ok(ProbeResult {
            port,
            outcome: "error",
            detail: err.to_string(),
            elapsed_ms: started.elapsed().as_millis(),
        }),
    }
}

fn print_help() {
    println!("Usage:");
    println!("  firewall-lab --target 192.168.64.4 --ports 22,80,443,3001");
    println!();
    println!("Options:");
    println!("  --target <ip-or-hostname>   Target host to probe");
    println!("  --ports <csv>               Comma-separated TCP ports");
    println!("  --timeout-ms <millis>       Connect timeout per port (default: 1500)");
}

fn main() {
    let (target, ports, timeout_ms) = match parse_args() {
        Ok(values) => values,
        Err(err) => {
            eprintln!("argument error: {err}");
            print_help();
            process::exit(2);
        }
    };

    println!("target={target} timeout_ms={timeout_ms}");
    println!("This probe checks TCP reachability at L4.");
    println!("HTTP status codes are not used to judge firewall behavior.");
    println!();

    for port in ports {
        match probe_port(&target, port, timeout_ms) {
            Ok(result) => {
                println!(
                    "port={} outcome={} elapsed_ms={} detail=\"{}\"",
                    result.port, result.outcome, result.elapsed_ms, result.detail
                );
            }
            Err(err) => {
                println!("port={port} outcome=resolution_error detail=\"{err}\"");
            }
        }
    }
}
