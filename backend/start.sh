#!/usr/bin/env bash
set -euo pipefail

export CLAMAV_DB_DIR="${CLAMAV_DB_DIR:-/var/lib/clamav}"
export CLAMAV_RUN_DIR="${CLAMAV_RUN_DIR:-/var/run/clamav}"
export CLAMAV_LOG_DIR="${CLAMAV_LOG_DIR:-/var/log/clamav}"

# On Railway, default to non-crashing startup.
# Set CLAMD_BOOT_MODE=required only if the service has enough memory.
export CLAMD_BOOT_MODE="${CLAMD_BOOT_MODE:-optional}"

mkdir -p "$CLAMAV_DB_DIR" "$CLAMAV_RUN_DIR" "$CLAMAV_LOG_DIR"
chown -R clamav:clamav "$CLAMAV_DB_DIR" "$CLAMAV_RUN_DIR" "$CLAMAV_LOG_DIR" || true
chmod 755 "$CLAMAV_RUN_DIR"

FRESHCLAM_CONF="/usr/local/etc/freshclam.conf"
CLAMD_CONF="/usr/local/etc/clamd.conf"

cat > "$FRESHCLAM_CONF" <<EOF
DatabaseDirectory $CLAMAV_DB_DIR
UpdateLogFile $CLAMAV_LOG_DIR/freshclam.log
LogTime yes
DatabaseMirror database.clamav.net
Foreground false
EOF

cat > "$CLAMD_CONF" <<EOF
DatabaseDirectory $CLAMAV_DB_DIR
LocalSocket $CLAMAV_RUN_DIR/clamd.ctl
LocalSocketMode 666
FixStaleSocket yes
User clamav
Foreground true
LogTime yes
LogFile $CLAMAV_LOG_DIR/clamd.log

# Reduce clamd memory pressure
MaxThreads 1
MaxQueue 4
MaxScanSize 25M
MaxFileSize 25M
MaxRecursion 8
MaxFiles 500
Bytecode yes
EOF

echo "Updating ClamAV database in $CLAMAV_DB_DIR..."

set +e
freshclam --config-file="$FRESHCLAM_CONF" --datadir="$CLAMAV_DB_DIR"
FRESHCLAM_EXIT=$?
set -e

echo "ClamAV DB directory contents after freshclam:"
find "$CLAMAV_DB_DIR" -maxdepth 1 -type f -print || true

if ! find "$CLAMAV_DB_DIR" -maxdepth 1 -type f \
  \( -name "*.cvd" -o -name "*.cld" -o -name "*.cud" -o -name "*.hdb" -o -name "*.ldb" -o -name "*.ndb" \) \
  | grep -q .; then
  echo "No ClamAV database files found in $CLAMAV_DB_DIR after freshclam"

  if [ "$CLAMD_BOOT_MODE" = "required" ]; then
    exit 1
  fi
fi

if [ "$FRESHCLAM_EXIT" -ne 0 ]; then
  echo "freshclam exited with code $FRESHCLAM_EXIT"
fi

echo "Starting clamd..."
set +e
clamd --config-file="$CLAMD_CONF" &
CLAMD_PID=$!
set -e

CLAMD_READY=0

for i in $(seq 1 60); do
  if [ -S "$CLAMAV_RUN_DIR/clamd.ctl" ]; then
    echo "clamd is ready"
    CLAMD_READY=1
    break
  fi

  if ! kill -0 "$CLAMD_PID" >/dev/null 2>&1; then
    echo "clamd exited before creating socket"
    cat "$CLAMAV_LOG_DIR/clamd.log" || true
    break
  fi

  echo "Waiting for clamd socket..."
  sleep 1
done

if [ "$CLAMD_READY" != "1" ]; then
  if [ "$CLAMD_BOOT_MODE" = "required" ]; then
    echo "clamd is required but failed to start"
    exit 1
  fi

  echo "clamd is unavailable; continuing web startup with best-effort malware scanning"
  export UPLOAD_MALWARE_SCAN_MODE="${UPLOAD_MALWARE_SCAN_MODE:-best_effort}"
else
  export UPLOAD_MALWARE_SCAN_MODE="${UPLOAD_MALWARE_SCAN_MODE:-required}"
fi

export UPLOAD_MALWARE_SCANNER="${UPLOAD_MALWARE_SCANNER:-clamdscan}"
export UPLOAD_MALWARE_SCAN_TIMEOUT_SECONDS="${UPLOAD_MALWARE_SCAN_TIMEOUT_SECONDS:-30}"

exec uvicorn api.api_v1:app --host 0.0.0.0 --port "${PORT:-8080}"