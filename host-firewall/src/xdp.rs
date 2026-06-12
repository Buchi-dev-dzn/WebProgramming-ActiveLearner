use std::convert::TryInto;
use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};

use aya::programs::Xdp;
use aya::{EbpfLoader, Pod};
use host_firewall_common::FirewallConfig;

use crate::XdpMode;

#[repr(transparent)]
#[derive(Clone, Copy)]
struct PodFirewallConfig(FirewallConfig);

unsafe impl Pod for PodFirewallConfig {}

pub fn apply_xdp(
    iface: &str,
    object_path: &str,
    pin_path: &str,
    mode: XdpMode,
    config: &FirewallConfig,
) -> Result<(), String> {
    let object = Path::new(object_path);
    if !object.exists() {
        return Err(format!(
            "XDP object not found: {} (build host-firewall-ebpf first)",
            object.display()
        ));
    }

    let pin = Path::new(pin_path);
    let parent = pin
        .parent()
        .ok_or_else(|| format!("invalid pin path: {}", pin.display()))?;
    fs::create_dir_all(parent).map_err(|err| {
        format!(
            "failed to create bpffs directory {}: {err}",
            parent.display()
        )
    })?;

    if pin.exists() {
        fs::remove_file(pin)
            .map_err(|err| format!("failed to replace pinned program {}: {err}", pin.display()))?;
    }

    let mut bpf = EbpfLoader::new()
        .set_global("HOST_FIREWALL_CONFIG", &PodFirewallConfig(*config), true)
        .load_file(object)
        .map_err(|err| format!("failed to load XDP object {}: {err}", object.display()))?;

    let program: &mut Xdp = bpf
        .program_mut("host_firewall_xdp")
        .ok_or_else(|| "XDP program `host_firewall_xdp` not found in object".to_string())?
        .try_into()
        .map_err(|err| format!("failed to get XDP program handle: {err}"))?;

    program
        .load()
        .map_err(|err| format!("failed to load XDP program into kernel: {err}"))?;
    program
        .pin(pin)
        .map_err(|err| format!("failed to pin XDP program {}: {err}", pin.display()))?;

    detach_link_only(iface)?;

    let status = Command::new("ip")
        .args([
            "link",
            "set",
            "dev",
            iface,
            ip_mode_name(mode),
            "pinned",
            pin_path,
        ])
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .map_err(|err| format!("failed to run ip link for XDP attach: {err}"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!("ip link XDP attach exited with status {status}"))
    }
}

pub fn detach_xdp(iface: &str, pin_path: &str) -> Result<(), String> {
    detach_link_only(iface)?;

    let pin = Path::new(pin_path);
    if pin.exists() {
        fs::remove_file(pin).map_err(|err| {
            format!(
                "failed to remove pinned XDP program {}: {err}",
                pin.display()
            )
        })?;
    }

    Ok(())
}

pub fn ip_mode_name(mode: XdpMode) -> &'static str {
    match mode {
        XdpMode::Generic => "xdpgeneric",
        XdpMode::Native => "xdpdrv",
        XdpMode::Offload => "xdpoffload",
    }
}

fn detach_link_only(iface: &str) -> Result<(), String> {
    let status = Command::new("ip")
        .args(["link", "set", "dev", iface, "xdp", "off"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|err| format!("failed to run ip link for XDP detach: {err}"))?;

    if status.success() || status.code() == Some(2) {
        Ok(())
    } else {
        Err(format!("ip link XDP detach exited with status {status}"))
    }
}
