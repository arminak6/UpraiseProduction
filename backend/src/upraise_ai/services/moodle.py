import ipaddress
import socket
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import certifi
import urllib3

from upraise_ai.core.errors import IngestionError


@contextmanager
def download_pdf(url: str, allowed_cidrs: list[str] | None = None):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise IngestionError("INVALID_MOODLE_URL", status=400) from None
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise IngestionError("MOODLE_URL_NOT_ALLOWED", status=403)
    try:
        addresses = {a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        permitted = [ipaddress.ip_network(cidr) for cidr in allowed_cidrs or []]
        if not addresses or any(
            ipaddress.ip_address(a).is_loopback
            or ipaddress.ip_address(a).is_link_local
            or ipaddress.ip_address(a).is_multicast
            or ipaddress.ip_address(a).is_unspecified
            or (
                not ipaddress.ip_address(a).is_global
                and not any(ipaddress.ip_address(a) in cidr for cidr in permitted)
            )
            for a in addresses
        ):
            raise IngestionError("MOODLE_ADDRESS_NOT_ALLOWED", status=403)
    except socket.gaierror:
        raise IngestionError("MOODLE_DNS_UNAVAILABLE", retryable=True, status=503) from None
    # Pin the validated IP while checking the original TLS hostname (DNS rebinding).
    pool = urllib3.HTTPSConnectionPool(
        sorted(addresses, key=lambda a: (ipaddress.ip_address(a).version, a))[0],
        port=443,
        server_hostname=host,
        assert_hostname=host,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        timeout=urllib3.Timeout(connect=10, read=30),
    )
    response = None
    try:
        target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        response = pool.urlopen(
            "GET",
            target,
            headers={"Host": host, "Accept": "application/pdf", "Accept-Encoding": "identity"},
            preload_content=False,
            redirect=False,
            retries=False,
        )
        if response.status != 200:
            raise IngestionError("MOODLE_DOWNLOAD_FAILED", status=502)
        if response.headers.get("Content-Encoding", "identity") != "identity":
            raise IngestionError("MOODLE_ENCODING_UNSUPPORTED", status=415)
        started = time.monotonic()

        class Reader:
            def read(self, amount):
                if time.monotonic() - started > 180:
                    raise IngestionError("MOODLE_DOWNLOAD_TIMEOUT", status=504)
                return response.read(amount)

        yield Reader()
    except urllib3.exceptions.HTTPError:
        raise IngestionError("MOODLE_DOWNLOAD_UNAVAILABLE", status=502) from None
    finally:
        if response is not None:
            response.close()
        pool.close()
