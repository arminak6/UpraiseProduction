"""Exercise the disposable login, token issuer and backend over real HTTP and TLS."""

from unittest.mock import patch
from uuid import uuid4

import httpx
from demo import issue_token
from transport import COURSE, client_tls, sign_access_token


def check(browser, issuer, directory, passwords):
    ui = f"http://127.0.0.1:{browser.server_port}"
    with httpx.Client(base_url=ui, trust_env=False) as client:
        assert client.get("/").status_code == 200
        assert client.get("/styles.css").status_code == 200
        assert client.get("/demo.js").status_code == 200
        assert client.get("/upraise-logo.png").headers["Content-Type"] == "image/png"
        assert client.get("/upraise-icon.png").headers["Content-Type"] == "image/png"
        assert client.get("/users.json").status_code == 404
        assert client.get("/demo/documents").status_code == 401
        assert client.post("/demo/login", json={"username": "teacher", "password": "wrong"}).status_code == 401
        response = client.post("/demo/login", json={"username": "teacher", "password": passwords["teacher"]},
                               headers={"Origin": "https://other.example"})
        assert response.status_code == 403
        response = client.post("/demo/login", json={"username": "teacher", "password": passwords["teacher"]})
        assert response.status_code == 200 and response.json() == {"user": "Teacher"}
        assert "HttpOnly" in response.headers["Set-Cookie"] and "SameSite=Strict" in response.headers["Set-Cookie"]
        result = client.get("/demo/documents")
        assert result.status_code == 200, result.text
        assert result.json()["body"][0]["course_id"] == COURSE
        result = client.get("/demo/version")
        assert result.status_code == 403 and result.json()["body"]["detail"] == "DEMO_ACCESS_DENIED"
        cookie = client.cookies.get("upraise_auth_demo")
        assert client.post("/demo/logout", json={}).status_code == 200
        response = client.get("/demo/documents", headers={"Cookie": f"upraise_auth_demo={cookie}"})
        assert response.status_code == 401
        response = client.post("/demo/login", json={"username": "operator", "password": passwords["operator"]})
        assert response.json() == {"user": "Operator"}
        assert client.get("/demo/version").status_code == 200
        result = client.get("/demo/documents")
        assert result.status_code == 403 and result.json()["body"]["detail"] == "DEMO_ACCESS_DENIED"
    print("PASS: login, wrong password, same-origin check, logout and both users' API permissions", flush=True)

    with httpx.Client(verify=client_tls(directory), trust_env=False) as client:
        result = client.post(issuer.issuer + "/oauth/token", data={"grant_type": "client_credentials"})
        assert result.status_code == 401 and result.json()["error"] == "invalid_client"
        token = issue_token(client, issuer.issuer, issuer.client_secret)
        try:
            client.get(browser.gateway + "/v1/documents", headers={"Authorization": f"Bearer {token}"})
        except httpx.TransportError:
            pass
        else:
            raise AssertionError("The mTLS gateway accepted a connection without a client certificate")
    print("PASS: OAuth client credentials and required TLS client certificate", flush=True)

    with httpx.Client(verify=client_tls(directory, mtls=True), trust_env=False) as client:
        endpoint = browser.gateway + "/v1/documents"
        missing = client.get(endpoint)
        assert missing.status_code == 401 and missing.json()["detail"] == "BEARER_TOKEN_REQUIRED"
        expired = sign_access_token(issuer.signing_key, issuer.issuer, lifetime=-60)
        for invalid in (expired, "not-a-jwt"):
            result = client.get(endpoint, headers={"Authorization": f"Bearer {invalid}"})
            assert result.status_code == 401 and result.json()["detail"] == "INVALID_ACCESS_TOKEN"
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(endpoint, headers=headers).status_code == 200
        result = client.get(endpoint, params={"course_id": "other-course"}, headers=headers)
        assert result.status_code == 403 and result.json()["detail"] == "COURSE_SCOPE_DENIED"
    print("PASS: real backend accepts signed JWT and rejects missing, malformed or expired JWT and wrong course", flush=True)


def check_workspace(browser, passwords):
    with httpx.Client(base_url=f"http://127.0.0.1:{browser.server_port}", trust_env=False) as client:
        response = client.get("/")
        assert response.status_code == 303 and response.headers["location"] == "/login"
        login_page = client.get("/login").text
        assert "Sign in" in login_page and "/upraise-logo.png" in login_page
        assert client.get("/upraise-logo.png").headers["Content-Type"] == "image/png"
        assert client.get("/upraise-icon.png").headers["Content-Type"] == "image/png"
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/v1/documents").status_code == 401
        form = {"username": "teacher", "password": "wrong"}
        assert client.post("/demo/login", json=form).status_code == 401
        assert client.post("/demo/login", json={**form, "password": passwords["teacher"]}).status_code == 200
        workspace_page = client.get("/").text
        assert "Teacher review" in workspace_page and "./upraise-logo.png" in workspace_page
        config = client.get("/api/config").json()
        assert config["demo_user"] == "Teacher" and config["demo_course"] == COURSE
        headers = {"X-UPRAISE-UI": config["ui_nonce"]}
        for path in ("documents", "lesson-jobs", "lesson-reviews"):
            result = client.get(f"/api/v1/{path}", headers=headers)
            assert result.status_code == 200 and isinstance(result.json(), list), result.text
        documents = f"/api/v1/documents?course_id={COURSE}"
        with patch.object(browser, "client_secret", "invalid-test-credential"):
            result = client.get(documents, headers=headers)
            assert result.status_code == 502 and result.json()["detail"] == "BACKEND_UNAVAILABLE", result.text
        assert client.get(documents, headers=headers).status_code == 200
        bad_course = client.get("/api/v1/documents", params={"course_id": "other"}, headers=headers)
        assert bad_course.status_code == 403 and bad_course.json()["detail"] == "COURSE_SCOPE_DENIED"

        source = uuid4().hex

        def try_upload():
            return client.post("/api/v1/documents", headers=headers, files={"file": ("auth-check.csv", b"item\none\n", "text/csv")},
                               data={"course_id": COURSE, "activity_id": "auth-check", "language": "en",
                                     "source_ref": source, "content_hash": source})

        result = try_upload()
        assert result.status_code == 202, result.text
        document_id, job_id = (result.json()[key] for key in ("document_id", "job_id"))
        job = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert job.status_code == 200 and job.json()["status"] == "QUEUED", job.text
        cleanup = client.delete(f"/api/v1/documents/{document_id}", headers=headers)
        assert cleanup.status_code == 204, cleanup.text
        assert client.post("/demo/logout", json={}).status_code == 200
        assert client.get("/api/v1/documents", headers=headers).status_code == 401
        assert client.post("/demo/login", json={"username": "operator", "password": passwords["operator"]}).status_code == 200
        result = try_upload()
        assert result.status_code == 403 and result.json()["detail"] == "DEMO_ACCESS_DENIED", result.text
    print("PASS: branded login and workspace, JWT issuance and recovery, API permissions and sign-out", flush=True)
