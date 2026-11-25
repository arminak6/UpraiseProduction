"""Serve a local OAuth token request and a sample UPRAISE API call for curl."""

import argparse

from demo import demo
from transport import CLIENT_ID, DemoServer, TokenIssuer


class CurlDemo(TokenIssuer):
    def do_GET(self):
        if self.path != "/v1/documents":
            self.json_response(404, {"detail": "DEMO_ROUTE_NOT_FOUND"})
            return

        response = self.server.client.get(
            self.server.gateway + self.path,
            headers={"Authorization": self.headers.get("Authorization", "")},
        )
        headers = {}
        if challenge := response.headers.get("WWW-Authenticate"):
            headers["WWW-Authenticate"] = challenge
        self.respond(response.status_code, response.content, response.headers["Content-Type"], headers=headers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8514)
    args = parser.parse_args()

    with demo("127.0.0.1", 0) as (browser, issuer, _, _), DemoServer((args.host, args.port), CurlDemo) as server:
        server.client = browser.client
        server.gateway = browser.gateway
        server.issuer = issuer.issuer
        server.signing_key = issuer.signing_key
        server.client_secret = issuer.client_secret
        server.scopes = {"documents:read"}

        print(f"UPRAISE curl demo listening on {args.host}:{server.server_port}", flush=True)
        print(f"Test client ID: {CLIENT_ID}", flush=True)
        print(f"Test client secret: {server.client_secret}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
