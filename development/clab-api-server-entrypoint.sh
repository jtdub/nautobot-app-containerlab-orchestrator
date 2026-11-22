#!/bin/sh
# Make the dev host account, lock the image account, and start clab-api-server.
# docker-compose.clab.yml mounts this file. See "Local clab-api-server" in docs/dev/dev_environment.md.
set -eu

: "${CLAB_DEV_HOST_USERNAME:?Set CLAB_DEV_HOST_USERNAME in development/creds.env.}"
: "${CLAB_DEV_HOST_PASSWORD:?Set CLAB_DEV_HOST_PASSWORD in development/creds.env.}"

# The server is reachable from the network, so do not start with the public example password.
if [ "$CLAB_DEV_HOST_PASSWORD" = "changeme" ]; then
  echo "Change CLAB_DEV_HOST_PASSWORD in development/creds.env. The example value is public." >&2
  exit 1
fi

groupadd -f clab_api
id "$CLAB_DEV_HOST_USERNAME" >/dev/null 2>&1 || useradd -m -G clab_api "$CLAB_DEV_HOST_USERNAME"
printf '%s:%s\n' "$CLAB_DEV_HOST_USERNAME" "$CLAB_DEV_HOST_PASSWORD" | chpasswd

# The image sets the public password "admin" for the account admin. Lock it.
passwd -l admin >/dev/null

# The image file /app/.env has a public JWT secret. The server uses the env value first.
if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  export JWT_SECRET
fi

exec /usr/local/bin/entrypoint.sh clab-api-server
