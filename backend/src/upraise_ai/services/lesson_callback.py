"""Pinned HTTPS delivery with mutual TLS and fresh OAuth2 client-credentials tokens."""

import base64
import ipaddress
import json
import socket
from urllib.parse import quote_plus, urlencode, urlsplit

import certifi
import urllib3

from upraise_ai.core.errors import IngestionError


def validate_https(url, allowed_hosts):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and host in {h.lower() for h in allowed_hosts}
                 and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                 and not parsed.fragment and not any(ord(char) < 32 or char.isspace() for char in url))
    except ValueError:
        valid = False
    if not valid:
        raise IngestionError("CALLBACK_URL_NOT_ALLOWED", status=403)
    return parsed


def validate_callback_config(settings, url):
    validate_https(url, settings.callback_allowed_hosts)
    if not all((settings.callback_token_url, settings.callback_client_id, settings.callback_certificate,
                settings.callback_private_key, settings.lesson_public_base_url)):
        raise IngestionError("LESSON_CALLBACK_NOT_CONFIGURED", status=503)
    if settings.callback_oauth_auth_method != "tls_client_auth" and not settings.callback_client_secret.get_secret_value():
        raise IngestionError("LESSON_CALLBACK_NOT_CONFIGURED", status=503)
    try:
        token_host = urlsplit(settings.callback_token_url).hostname
        validate_https(settings.callback_token_url, [token_host] if token_host else [])
        base_host = urlsplit(settings.lesson_public_base_url).hostname
        validate_https(settings.lesson_public_base_url, [base_host] if base_host else [])
    except ValueError:
        raise IngestionError("LESSON_CALLBACK_NOT_CONFIGURED", status=503) from None


class CallbackClient:
    def __init__(self, settings):
        self.settings = settings

    def post(self, url, data, headers, *, token_endpoint=False):
        hosts = ([urlsplit(self.settings.callback_token_url).hostname] if token_endpoint
                 else self.settings.callback_allowed_hosts)
        parsed = validate_https(url, hosts)
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror:
            raise IngestionError("CALLBACK_DNS_UNAVAILABLE", retryable=True) from None
        permitted = [ipaddress.ip_network(cidr) for cidr in self.settings.callback_allowed_cidrs]
        if not addresses or any(
            (address := ipaddress.ip_address(value)).is_loopback or address.is_link_local
            or address.is_multicast or address.is_unspecified or
            (not address.is_global and not any(address in cidr for cidr in permitted)) for value in addresses
        ):
            raise IngestionError("CALLBACK_ADDRESS_NOT_ALLOWED")
        pool = urllib3.HTTPSConnectionPool(
            sorted(addresses)[0], port=443, server_hostname=parsed.hostname, assert_hostname=parsed.hostname,
            cert_reqs="CERT_REQUIRED", ca_certs=str(self.settings.callback_ca_file or certifi.where()),
            cert_file=str(self.settings.callback_certificate), key_file=str(self.settings.callback_private_key),
            timeout=urllib3.Timeout(connect=10, read=20),
        )
        response = None
        try:
            target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            response = pool.urlopen("POST", target, body=data, headers={"Host": parsed.hostname, **headers},
                                    redirect=False, retries=False, preload_content=False)
            body = response.read(1_048_577)
            if len(body) > 1_048_576 or not 200 <= response.status < 300:
                raise IngestionError("CALLBACK_HTTP_FAILED", retryable=True)
            return body
        except urllib3.exceptions.HTTPError:
            raise IngestionError("CALLBACK_HTTP_UNAVAILABLE", retryable=True) from None
        finally:
            if response is not None:
                response.close()
            pool.close()

    def deliver(self, row, result):
        validate_callback_config(self.settings, row["metadata"]["callback_url"])
        fields = {"grant_type": "client_credentials", "scope": self.settings.callback_scope}
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        method = self.settings.callback_oauth_auth_method
        if method == "client_secret_basic":
            credentials = (quote_plus(self.settings.callback_client_id) + ":" +
                           quote_plus(self.settings.callback_client_secret.get_secret_value())).encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode()
        else:
            fields["client_id"] = self.settings.callback_client_id
            if method == "client_secret_post":
                fields["client_secret"] = self.settings.callback_client_secret.get_secret_value()
        token_response = self.post(self.settings.callback_token_url, urlencode(fields).encode(), headers,
                                   token_endpoint=True)
        try:
            token = json.loads(token_response)
            access = token["access_token"]
            lifetime = token["expires_in"]
            if (token["token_type"].lower() != "bearer" or not isinstance(access, str)
                    or not access or len(access) > 16384 or any(c.isspace() for c in access)
                    or isinstance(lifetime, bool) or not isinstance(lifetime, (int, float))
                    or not 30 < lifetime <= self.settings.callback_max_token_lifetime_seconds):
                raise ValueError("invalid short-lived token")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise IngestionError("CALLBACK_OAUTH_INVALID_RESPONSE") from None
        payload = {"job_id": str(row["job_id"]), "lesson_id": row["metadata"]["lesson_id"],
                   "status": row["status"], "result": result,
                   "error": {"code": row["error_code"]} if row["error_code"] else None}
        self.post(row["metadata"]["callback_url"], json.dumps(payload, ensure_ascii=False).encode(),
                  {"Authorization": "Bearer " + access, "Content-Type": "application/json",
                   "Idempotency-Key": "lesson-result-" + str(row["job_id"]),
                   "X-UPRAISE-Job-ID": str(row["job_id"])})
