

## 1. Request a JWT

Send a `POST` request to `http://127.0.0.1:8000/oauth/token` with these inputs:

| Input | Value |
| --- | --- |
| Client ID | `auth-demo-middleware` |
| Client secret | Provided |
| `grant_type` | `client_credentials` |



Replace `YOUR_CLIENT_SECRET` with the client secret:

```powershell
curl.exe -u "auth-demo-middleware:YOUR_CLIENT_SECRET" -d "grant_type=client_credentials" http://127.0.0.1:8000/oauth/token
```

The current client secret is `f9fe6e0a8880439ab6b18bab46ec6cdc22a06637b2e34b6d948091a21a2bccfd`. For the current local setup:

```powershell
curl.exe -u "auth-demo-middleware:f9fe6e0a8880439ab6b18bab46ec6cdc22a06637b2e34b6d948091a21a2bccfd" -d "grant_type=client_credentials" http://127.0.0.1:8000/oauth/token
```

The server returns:

```json
{
  "access_token": "eyJ...your-JWT...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

The value of `access_token` is the JWT. 
## 2. Call a backend API

To call a protected backend API, send your JWT in the
`Authorization: Bearer` header.

For example, to call `GET /v1/documents` and list accessible documents, use
this command. Replace `YOUR_ACCESS_TOKEN` with the JWT returned in the first
step:

```powershell
curl.exe -H "Authorization: Bearer YOUR_ACCESS_TOKEN" "http://127.0.0.1:8000/v1/documents"
```

