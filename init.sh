#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -e .env ]; then
  printf '%s\n' '.env already exists; preserving settings.'
else
  umask 077
  admin_secret=$(od -An -N18 -tx1 /dev/urandom | tr -d ' \n')
  session_secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  printf 'HTTP_PORT=5004\nADMIN_USERNAME=admin\nADMIN_PASSWORD=%s\nSECRET_KEY=%s\nPANSOU_URL=\n' "$admin_secret" "$session_secret" > .env
  printf '%s\n' 'Generated unique credentials in .env. Open that file to view your admin password.'
fi
mkdir -p data logs sync-state secrets
if [ ! -e secrets/tencent-token ]; then (umask 077; : > secrets/tencent-token); fi
printf '%s\n' 'Ready. Run: docker compose up -d --build'
