#![no_std]
#![no_main]

use core::mem;
use core::panic::PanicInfo;
use core::ptr;

use aya_ebpf::bindings::xdp_action;
use aya_ebpf::macros::xdp;
use aya_ebpf::programs::XdpContext;
use host_firewall_common::FirewallConfig;

const ETH_P_IP: u16 = 0x0800;
const ETH_P_IPV6: u16 = 0x86dd;
const IPPROTO_ICMP: u8 = 1;
const IPPROTO_TCP: u8 = 6;
const IPPROTO_ICMPV6: u8 = 58;

#[no_mangle]
static HOST_FIREWALL_CONFIG: FirewallConfig = FirewallConfig::empty();

#[repr(C, packed)]
struct EthHdr {
    dst: [u8; 6],
    src: [u8; 6],
    ether_type: u16,
}

#[repr(C, packed)]
struct Ipv4Hdr {
    version_ihl: u8,
    tos: u8,
    total_length: u16,
    id: u16,
    frag_off: u16,
    ttl: u8,
    protocol: u8,
    checksum: u16,
    src_addr: [u8; 4],
    dst_addr: [u8; 4],
}

#[repr(C, packed)]
struct Ipv6Hdr {
    version_tc_flow: u32,
    payload_len: u16,
    next_header: u8,
    hop_limit: u8,
    src_addr: [u8; 16],
    dst_addr: [u8; 16],
}

#[repr(C, packed)]
struct TcpHdr {
    src_port: u16,
    dst_port: u16,
    seq: u32,
    ack_seq: u32,
    offset_flags: u16,
    window: u16,
    checksum: u16,
    urgent_ptr: u16,
}

#[xdp]
pub fn host_firewall_xdp(ctx: XdpContext) -> u32 {
    match try_host_firewall_xdp(ctx) {
        Ok(action) => action,
        Err(_) => xdp_action::XDP_ABORTED,
    }
}

fn try_host_firewall_xdp(ctx: XdpContext) -> Result<u32, ()> {
    let ethhdr: *const EthHdr = unsafe { ptr_at(&ctx, 0)? };
    let ether_type = u16::from_be(unsafe { ptr::read_unaligned(ptr::addr_of!((*ethhdr).ether_type)) });

    match ether_type {
        ETH_P_IP => handle_ipv4(ctx),
        ETH_P_IPV6 => handle_ipv6(ctx),
        _ => Ok(xdp_action::XDP_PASS),
    }
}

fn handle_ipv4(ctx: XdpContext) -> Result<u32, ()> {
    let ip_offset = mem::size_of::<EthHdr>();
    let ipv4hdr: *const Ipv4Hdr = unsafe { ptr_at(&ctx, ip_offset)? };
    let version_ihl = unsafe { ptr::read_unaligned(ptr::addr_of!((*ipv4hdr).version_ihl)) };
    let ihl = (version_ihl & 0x0f) as usize * 4;

    if ihl < mem::size_of::<Ipv4Hdr>() {
        return Ok(xdp_action::XDP_DROP);
    }

    let protocol = unsafe { ptr::read_unaligned(ptr::addr_of!((*ipv4hdr).protocol)) };
    let config = unsafe { ptr::read_volatile(&HOST_FIREWALL_CONFIG) };

    match protocol {
        IPPROTO_TCP => {
            let tcp_offset = ip_offset + ihl;
            let tcphdr: *const TcpHdr = unsafe { ptr_at(&ctx, tcp_offset)? };
            let dst_port = u16::from_be(unsafe {
                ptr::read_unaligned(ptr::addr_of!((*tcphdr).dst_port))
            });
            if port_allowed(&config, dst_port) {
                Ok(xdp_action::XDP_PASS)
            } else {
                Ok(xdp_action::XDP_DROP)
            }
        }
        IPPROTO_ICMP => Ok(xdp_action::XDP_PASS),
        _ => Ok(xdp_action::XDP_DROP),
    }
}

fn handle_ipv6(ctx: XdpContext) -> Result<u32, ()> {
    let ip_offset = mem::size_of::<EthHdr>();
    let ipv6hdr: *const Ipv6Hdr = unsafe { ptr_at(&ctx, ip_offset)? };
    let next_header = unsafe { ptr::read_unaligned(ptr::addr_of!((*ipv6hdr).next_header)) };
    let config = unsafe { ptr::read_volatile(&HOST_FIREWALL_CONFIG) };

    match next_header {
        IPPROTO_TCP => {
            let tcp_offset = ip_offset + mem::size_of::<Ipv6Hdr>();
            let tcphdr: *const TcpHdr = unsafe { ptr_at(&ctx, tcp_offset)? };
            let dst_port = u16::from_be(unsafe {
                ptr::read_unaligned(ptr::addr_of!((*tcphdr).dst_port))
            });
            if port_allowed(&config, dst_port) {
                Ok(xdp_action::XDP_PASS)
            } else {
                Ok(xdp_action::XDP_DROP)
            }
        }
        IPPROTO_ICMPV6 => Ok(xdp_action::XDP_PASS),
        _ => Ok(xdp_action::XDP_DROP),
    }
}

fn port_allowed(config: &FirewallConfig, dst_port: u16) -> bool {
    let mut index = 0;
    while index < config.allowed_tcp_ports_len as usize {
        if config.allowed_tcp_ports[index] == dst_port {
            return true;
        }
        index += 1;
    }
    false
}

unsafe fn ptr_at<T>(ctx: &XdpContext, offset: usize) -> Result<*const T, ()> {
    let start = ctx.data();
    let end = ctx.data_end();
    let len = mem::size_of::<T>();

    if start + offset + len > end {
        return Err(());
    }

    Ok((start + offset) as *const T)
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}
