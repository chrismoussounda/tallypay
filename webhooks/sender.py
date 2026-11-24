import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from urllib.parse import SplitResult, urlsplit

from django.conf import settings

# For each wait, then for all of them together.
TIMEOUT = 10
LIMIT = 15


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


def _look_up(host: str, port: int) -> str:
    """Has no time limit of its own, so it is left behind when it takes too long."""
    pool = ThreadPoolExecutor(1)
    try:
        return pool.submit(_resolve, host, port).result(TIMEOUT)
    finally:
        pool.shutdown(wait=False)


@contextmanager
def _cut_after(sock: socket.socket, seconds: float) -> Iterator[threading.Event]:
    """Cuts the connection when the time is up, even while bytes still arrive."""
    late = threading.Event()

    def cut() -> None:
        late.set()
        with suppress(OSError):
            # Not the TLS one, which is not safe to call from another thread.
            socket.socket.shutdown(sock, socket.SHUT_RDWR)

    timer = threading.Timer(seconds, cut)
    timer.start()
    try:
        yield late
    finally:
        timer.cancel()


def send(url: str, headers: dict[str, str], body: bytes) -> int:
    """The status answered. Redirects are not followed."""
    parts = check(url)
    host = parts.hostname or ""
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    started = time.monotonic()
    try:
        # Connects to the address that was checked: looked up again, it could differ.
        sock = socket.create_connection((_look_up(host, port), port), TIMEOUT)
        connection.sock = sock
        if secure:
            connection.sock = sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=host, do_handshake_on_connect=False
            )
        left = max(LIMIT - (time.monotonic() - started), 0)
        with _cut_after(sock, left) as late:
            try:
                if isinstance(sock, ssl.SSLSocket):
                    sock.do_handshake()
                connection.request(
                    "POST", path, body, {"Content-Type": "application/json", **headers}
                )
                status = connection.getresponse().status
            except (OSError, http.client.HTTPException) as error:
                if late.is_set():
                    raise TimeoutError from error
                raise
            # An answer cut short can look complete.
            if late.is_set():
                raise TimeoutError
            return status
    except TimeoutError as error:
        raise Unreachable("No answer in time.") from error
    except ssl.SSLError as error:
        raise Unreachable("The certificate is not valid.") from error
    except (OSError, http.client.HTTPException) as error:
        raise Unreachable("The connection failed.") from error
    finally:
        connection.close()
