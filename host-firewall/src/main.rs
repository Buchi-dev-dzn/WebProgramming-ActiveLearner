mod config;
mod nft;
mod settings;
mod xdp;

use std::env;
use std::process::{self, Command};

use config::{build_firewall_config, parse_ports, validate_table_name, FirewallConfigError};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Backend {
    Nft,
    Xdp,
}

#[allow(dead_code)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate)
enum XdpMode {
    Generic,
    Native,
    Offload,
}

#[derive(Debug)]
struct Args {
    apply: bool,
    detach: bool,
    backend: Backend,
}

fn main() {
    let args = match parse_args() {
        Ok(args) => args,
        Err(err) => {
            eprintln!("argument error: {err}");
            print_help();
            process::exit(2);
        }
    };

    let exit_code = match run(&args) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("{err}");
            1
        }
    };

    process::exit(exit_code);
}

fn run(args: &Args) -> Result<(), String> {
    match args.backend {
        Backend::Nft => run_nft(args),
        Backend::Xdp => run_xdp(args),
    }
}

fn run_nft(args: &Args) -> Result<(), String> {
    if args.detach {
        return Err("--detach is only supported with --backend xdp".to_string());
    }

    let rules = nft::build_rules(settings::TABLE_NAME, settings::ALLOWED_TCP_PORTS);

    if !args.apply {
        println!("{rules}");
        println!();
        println!("dry-run only; pass --apply to install this nftables table");
        return Ok(());
    }

    ensure_root()?;
    nft::apply_rules(settings::TABLE_NAME, &rules)?;
    println!(
        "applied nftables table={} allowed_tcp_ports={:?}",
        settings::TABLE_NAME,
        settings::ALLOWED_TCP_PORTS
    );
    Ok(())
}

fn run_xdp(args: &Args) -> Result<(), String> {
    let iface = settings::XDP_IFACE.ok_or_else(|| "XDP_IFACE is not configured in src/settings.rs".to_string())?;

    if !args.apply {
        print_xdp_preview(args, iface)?;
        return Ok(());
    }

    ensure_root()?;

    if args.detach {
        xdp::detach_xdp(iface, settings::XDP_PIN_PATH)?;
        println!(
            "detached xdp interface={} pin_path={}",
            iface,
            settings::XDP_PIN_PATH
        );
        return Ok(());
    }

    let config = build_firewall_config(settings::ALLOWED_TCP_PORTS).map_err(render_config_error)?;
    xdp::apply_xdp(
        iface,
        settings::XDP_OBJECT,
        settings::XDP_PIN_PATH,
        settings::XDP_MODE,
        &config,
    )?;

    println!(
        "applied xdp interface={} mode={} object={} pin_path={} allowed_tcp_ports={:?}",
        iface,
        xdp_mode_name(settings::XDP_MODE),
        settings::XDP_OBJECT,
        settings::XDP_PIN_PATH,
        settings::ALLOWED_TCP_PORTS
    );
    Ok(())
}

fn parse_args() -> Result<Args, String> {
    let mut apply = false;
    let mut detach = false;
    let mut backend = Backend::Nft;

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--apply" => apply = true,
            "--detach" => detach = true,
            "--backend" => {
                backend = match args
                    .next()
                    .ok_or_else(|| "missing value for --backend".to_string())?
                    .as_str()
                {
                    "nft" => Backend::Nft,
                    "xdp" => Backend::Xdp,
                    other => return Err(format!("unsupported backend: {other}")),
                };
            }
            "--help" | "-h" => {
                print_help();
                process::exit(0);
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }

    validate_table_name(settings::TABLE_NAME)?;
    parse_ports(&settings::allowed_tcp_ports_csv())?;

    Ok(Args {
        apply,
        detach,
        backend,
    })
}

fn print_xdp_preview(_args: &Args, iface: &str) -> Result<(), String> {
    let config = build_firewall_config(settings::ALLOWED_TCP_PORTS).map_err(render_config_error)?;

    println!("backend: xdp");
    println!("interface: {iface}");
    println!("mode: {}", xdp_mode_name(settings::XDP_MODE));
    println!("object: {}", settings::XDP_OBJECT);
    println!("pin_path: {}", settings::XDP_PIN_PATH);
    println!("rule_capacity: ports={}", config.allowed_tcp_ports_len);
    println!();
    println!("xdp dry-run notes:");
    println!("  - Stateless port whitelist only; no conntrack or established/related handling");
    println!("  - Non-IP traffic is passed through to avoid breaking ARP/NDP");
    println!("  - IPv6 extension headers are not parsed; those packets are dropped by default");
    println!("  - Build the eBPF object before apply mode");
    println!();
    println!("attach command:");
    println!(
        "  sudo ip link set dev {iface} {} pinned {}",
        xdp::ip_mode_name(settings::XDP_MODE),
        settings::XDP_PIN_PATH
    );
    println!("detach command:");
    println!("  sudo ip link set dev {iface} xdp off");
    println!();
    println!("dry-run only; pass --apply to pin and attach this XDP program");
    Ok(())
}

fn render_config_error(err: FirewallConfigError) -> String {
    err.to_string()
}

fn running_as_root() -> bool {
    match Command::new("id").arg("-u").output() {
        Ok(output) => String::from_utf8_lossy(&output.stdout).trim() == "0",
        Err(_) => false,
    }
}

fn ensure_root() -> Result<(), String> {
    if running_as_root() {
        Ok(())
    } else {
        Err("apply mode requires root".to_string())
    }
}

fn xdp_mode_name(mode: XdpMode) -> &'static str {
    match mode {
        XdpMode::Generic => "generic",
        XdpMode::Native => "native",
        XdpMode::Offload => "offload",
    }
}

fn print_help() {
    println!("Usage:");
    println!("  host-firewall [--backend nft|xdp] [--apply] [--detach]");
    println!();
    println!("Behavior:");
    println!("  - default backend is nft");
    println!("  - default is dry-run; rules or attach plan are printed only");
    println!("  - --apply installs the nftables table or pins and attaches the XDP program");
    println!("  - --detach is supported only with --backend xdp");
    println!("  - ports and XDP settings are defined in src/settings.rs");
    println!("  - packet filtering is a TCP destination port whitelist");
    println!("  - ICMP and ICMPv6 are allowed for reachability checks");
    println!("  - XDP mode is stateless and does not replicate conntrack behavior");
}
