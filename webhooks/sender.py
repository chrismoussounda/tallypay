import http.client
import ipaddress
import socket
import ssl
from urllib.parse import SplitResult, urlsplit

from django.conf import settings

TIMEOUT = 10


class Refused(Exception):
    """An address that webhooks may not be sent to."""


class Unreachable(Exception):
    pass


def check(url: str) -> SplitResult:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise Refused("This is not an address.") from error
    if not url.isascii() or not url.isprintable() or " " in url or not parts.hostname:
        raise Refused("This is not an address.")
    if parts.username is not None or parts.fragment:
        raise Refused("The address may not hold a user name or a fragment.")
    if settings.WEBHOOKS_ALLOW_LOCAL and parts.scheme == "http":
        return parts
    if parts.scheme != "https" or port not in (None, 443):
        raise Refused("The address must be https, on the usual port.")
    return parts


def _resolve(host: str, port: int) -> str:
    try:
        # IPv4 only: several kinds of IPv6 address lead to an internal IPv4 one.
        found = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise Unreachable("The host was not found.") from error
    addresses = [ipaddress.IPv4Address(entry[4][0]) for entry in found]
    for address in addresses:
        public = address.is_global and not address.is_multicast
        if not public and not settings.WEBHOOKS_ALLOW_LOCAL:
            raise Refused("The address is not a public one.")
    return str(addresses[0])


def send(url: str, headers: dict[str, str], body: bytes) -> int:
    """The status answered. Redirects are not followed."""
    parts = check(url)
    host = parts.hostname or ""
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    # Connects to the address that was checked: looked up again, it could differ.
    ip = _resolve(host, port)
    connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    try:
        sock = socket.create_connection((ip, port), TIMEOUT)
        if secure:
            context = ssl.create_default_context()
            try:
                sock = context.wrap_socket(sock, server_hostname=host)
            except BaseException:
                sock.close()
                raise
        connection.sock = sock
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        connection.request(
            "POST", path, body, {"Content-Type": "application/json", **headers}
        )
        return connection.getresponse().status
    except TimeoutError as error:
        raise Unreachable("No answer in time.") from error
    except ssl.SSLError as error:
        raise Unreachable("The certificate is not valid.") from error
    except (OSError, http.client.HTTPException) as error:
        raise Unreachable("The connection failed.") from error
    finally:
        connection.close()
