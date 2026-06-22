#!/usr/bin/env bash
set -e

mkdir -p /var/lib/clamav

freshclam || true

exec uvicorn api.api_v1:app --host 0.0.0.0 --port "${PORT:-8080}"