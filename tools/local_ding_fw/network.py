import ipaddress

ALLOWED_V4 = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "224.0.0.0/24",
    "239.255.255.250/32",
    "255.255.255.255/32",
)
DISABLED_BINARIES = ("cloud", "push", "netclient")
DISABLED_CAPABILITIES = (
    "support_uid",
    "support_push",
    "support_online_update",
    "support_cloud",
    "support_extern_stream_cloud",
    "support_push_with_pic",
    "support_ddns",
    "support_upnp",
    "support_autontp",
    "support_ntp",
)


def blocked_networks():
    remaining = [ipaddress.IPv4Network("0.0.0.0/0")]
    for prefix in ALLOWED_V4:
        allowed = ipaddress.IPv4Network(prefix)
        next_set = []
        for network in remaining:
            next_set.extend(
                network.address_exclude(allowed)
                if allowed.subnet_of(network)
                else [network]
            )
        remaining = next_set
    result = sorted(ipaddress.collapse_addresses(remaining))
    assert (
        sum(n.num_addresses for n in result)
        + sum(ipaddress.ip_network(n).num_addresses for n in ALLOWED_V4)
        == 2**32
    )
    return result
