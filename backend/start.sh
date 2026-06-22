#!/usr/bin/env bash
set -e

mkdir -p /var/run/clamav /var/lib/clamav
chown -R clamav:clamav /var/run/clamav /var/lib/clamav || true

freshclam || true

clamd --config-file=/etc/clamav/clamd.conf &

for i in $(seq 1 30); do
  if [ -S /var/run/clamav/clamd.ctl ]; then
    echo "ClamAV daemon is ready"
    break
  fi
  echo "Waiting for ClamAV daemon..."
  sleep 1
done

if [ ! -S /var/run/clamav/clamd.ctl ]; then
  echo "ClamAV socket was not created"
  exit 1
fi

exec uvicorn api.api_v1:app --host 0.0.0.0 --port "${PORT:-8080}"