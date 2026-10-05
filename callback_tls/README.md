Place the AI module's outbound client certificate, private key and optional MW
CA certificate here. This directory is mounted read-only into lesson-worker at
`/run/secrets/callback_tls`. PEM/key files are ignored by Git. Restrict host file
permissions; allow container UID 10001 to read the required files.

Configure the corresponding UPRAISE_CALLBACK_* paths and OAuth client credentials
in deployment secrets. Never use the Middleware's inbound private key.
