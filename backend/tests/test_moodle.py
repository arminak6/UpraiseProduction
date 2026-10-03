import io
import socket

import pytest

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.moodle import download_pdf


@pytest.fixture
def downloader(monkeypatch):
    class Response:
        status = 200
        headers = {"Content-Type": "text/plain"}

        def __init__(self):
            self.body = io.BytesIO(b"%PDF-1.7\nsource document")
            self.closed = False

        def read(self, amount):
            return self.body.read(amount)

        def close(self):
            self.closed = True

    class Pool:
        def __init__(self):
            self.response = Response()
            self.closed = False
            self.request = None

        def urlopen(self, method, target, **kwargs):
            self.request = (method, target, kwargs)
            return self.response

        def close(self):
            self.closed = True

    pool = Pool()
    connection = {}

    def create_pool(address, **kwargs):
        connection.update(address=address, **kwargs)
        return pool

    monkeypatch.setattr(
        "upraise_ai.services.moodle.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr("upraise_ai.services.moodle.urllib3.HTTPSConnectionPool", create_pool)
    return pool, connection


def test_public_signed_url_streams_despite_non_pdf_mime(downloader):
    pool, connection = downloader
    with download_pdf("https://new-moodle.example/material.pdf?token=signed") as stream:
        assert stream.read(1024) == b"%PDF-1.7\nsource document"
    assert connection["address"] == "8.8.8.8"
    assert connection["server_hostname"] == connection["assert_hostname"] == "new-moodle.example"
    assert connection["cert_reqs"] == "CERT_REQUIRED"
    method, target, options = pool.request
    assert method == "GET" and target == "/material.pdf?token=signed"
    assert options["headers"]["Host"] == "new-moodle.example"
    assert options["redirect"] is False
    assert pool.closed and pool.response.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://moodle.example/file.pdf",
        "https:///file.pdf",
        "https://user:password@moodle.example/file.pdf",
        "https://@moodle.example/file.pdf",
        "https://moodle.example:8443/file.pdf",
        "https://moodle.example/file.pdf#fragment",
    ],
)
def test_invalid_download_url_never_connects(url, downloader):
    pool, connection = downloader
    with pytest.raises(IngestionError, match="MOODLE_URL_NOT_ALLOWED"):
        with download_pdf(url):
            pytest.fail("Invalid URL was downloaded")
    assert connection == {} and pool.request is None


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1"])
def test_internal_addresses_require_network_policy(monkeypatch, downloader, address):
    pool, connection = downloader
    monkeypatch.setattr(
        "upraise_ai.services.moodle.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, (address, 443))],
    )
    with pytest.raises(IngestionError, match="MOODLE_ADDRESS_NOT_ALLOWED"):
        with download_pdf("https://moodle.example/file.pdf"):
            pytest.fail("Internal address was downloaded")
    assert connection == {} and pool.request is None


def test_explicit_private_moodle_network_can_download(monkeypatch, downloader):
    _, connection = downloader
    monkeypatch.setattr(
        "upraise_ai.services.moodle.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("10.20.0.5", 443))],
    )
    with download_pdf("https://moodle.example/file.pdf", ["10.20.0.0/24"]) as stream:
        assert stream.read(5) == b"%PDF-"
    assert connection["address"] == "10.20.0.5"


def test_redirect_response_is_rejected_and_closed(downloader):
    pool, _ = downloader
    pool.response.status = 302
    pool.response.headers = {"Location": "https://other.example/file.pdf"}
    with pytest.raises(IngestionError, match="MOODLE_DOWNLOAD_FAILED"):
        with download_pdf("https://moodle.example/file.pdf"):
            pytest.fail("Redirect response was accepted")
    assert pool.closed and pool.response.closed


def test_encoded_response_is_rejected(downloader):
    pool, _ = downloader
    pool.response.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(IngestionError, match="MOODLE_ENCODING_UNSUPPORTED"):
        with download_pdf("https://moodle.example/file.pdf"):
            pytest.fail("Encoded response was accepted")
    assert pool.closed and pool.response.closed


def test_unresolved_download_host_reports_retryable_error(monkeypatch, downloader):
    def unresolved(*args, **kwargs):
        raise socket.gaierror("Synthetic DNS failure")

    monkeypatch.setattr("upraise_ai.services.moodle.socket.getaddrinfo", unresolved)
    with pytest.raises(IngestionError, match="MOODLE_DNS_UNAVAILABLE") as error:
        with download_pdf("https://moodle.example/file.pdf"):
            pytest.fail("Unresolved host was downloaded")
    assert error.value.retryable is True
