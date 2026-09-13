import ipaddress
import socket


def _usable_lan_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
    )


def lan_ipv4_addresses() -> list[str]:
    """Return likely LAN addresses with the active default-route address first."""
    addresses: list[str] = []

    # A UDP connect chooses an interface locally; it sends no traffic and works
    # even when the LAN itself has no Internet access.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # RFC 5737 documentation-only address
            preferred = probe.getsockname()[0]
            if _usable_lan_ip(preferred):
                addresses.append(preferred)
    except OSError:
        pass

    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = result[4][0]
            if _usable_lan_ip(candidate) and candidate not in addresses:
                addresses.append(candidate)
    except OSError:
        pass

    return addresses


def print_lan_banner(port: int = 8000) -> None:
    addresses = lan_ipv4_addresses()
    line = "=" * 62
    print(f"\n{line}")
    print(" LAN Chat is ready")
    print(f" This laptop:  http://localhost:{port}")
    if addresses:
        print(f" Other devices: http://{addresses[0]}:{port}  <-- copy this")
        for address in addresses[1:]:
            print(f" Alternative:   http://{address}:{port}")
    else:
        print(" LAN address:    Could not detect one; check your Wi-Fi connection")
    print(" Keep this terminal open. Press Ctrl+C to stop the server.")
    print(f"{line}\n", flush=True)
