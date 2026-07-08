#!/usr/bin/env bash
set -euo pipefail

mkdir -p /var/lib/clamav /var/run/clamav /var/log/clamav
chmod 755 /var/run/clamav

# -----------------------------
# Locate or create freshclam.conf
# -----------------------------
FRESHCLAM_CONF=""

for candidate in \
  /usr/local/etc/freshclam.conf \
  /etc/clamav/freshclam.conf \
  /etc/freshclam.conf
do
  if [ -f "$candidate" ]; then
    FRESHCLAM_CONF="$candidate"
    break
  fi
done

if [ -z "$FRESHCLAM_CONF" ]; then
  for sample in \
    /usr/local/etc/freshclam.conf.sample \
    /etc/clamav/freshclam.conf.sample \
    /etc/freshclam.conf.sample
  do
    if [ -f "$sample" ]; then
      FRESHCLAM_CONF="${sample%.sample}"
      cp "$sample" "$FRESHCLAM_CONF"
      break
    fi
  done
fi

if [ -n "$FRESHCLAM_CONF" ]; then
  echo "Using freshclam config: $FRESHCLAM_CONF"
  sed -i 's/^Example/#Example/' "$FRESHCLAM_CONF" || true

  grep -q '^DatabaseDirectory ' "$FRESHCLAM_CONF" \
    && sed -i 's|^DatabaseDirectory .*|DatabaseDirectory /var/lib/clamav|' "$FRESHCLAM_CONF" \
    || echo 'DatabaseDirectory /var/lib/clamav' >> "$FRESHCLAM_CONF"

  freshclam --config-file="$FRESHCLAM_CONF" || true
else
  echo "freshclam.conf not found; trying freshclam default config"
  freshclam || true
fi

# -----------------------------
# Locate or create clamd.conf
# -----------------------------
CLAMD_CONF=""

for candidate in \
  /usr/local/etc/clamd.conf \
  /etc/clamav/clamd.conf \
  /etc/clamd.conf
do
  if [ -f "$candidate" ]; then
    CLAMD_CONF="$candidate"
    break
  fi
done

if [ -z "$CLAMD_CONF" ]; then
  for sample in \
    /usr/local/etc/clamd.conf.sample \
    /etc/clamav/clamd.conf.sample \
    /etc/clamd.conf.sample
  do
    if [ -f "$sample" ]; then
      CLAMD_CONF="${sample%.sample}"
      cp "$sample" "$CLAMD_CONF"
      break
    fi
  done
fi

if [ -z "$CLAMD_CONF" ]; then
  echo "clamd.conf not found and no clamd.conf.sample found"
  exit 1
fi

echo "Using clamd config: $CLAMD_CONF"

sed -i 's/^Example/#Example/' "$CLAMD_CONF" || true

grep -q '^DatabaseDirectory ' "$CLAMD_CONF" \
  && sed -i 's|^DatabaseDirectory .*|DatabaseDirectory /var/lib/clamav|' "$CLAMD_CONF" \
  || echo 'DatabaseDirectory /var/lib/clamav' >> "$CLAMD_CONF"

grep -q '^LocalSocket ' "$CLAMD_CONF" \
  && sed -i 's|^LocalSocket .*|LocalSocket /var/run/clamav/clamd.ctl|' "$CLAMD_CONF" \
  || echo 'LocalSocket /var/run/clamav/clamd.ctl' >> "$CLAMD_CONF"

grep -q '^LocalSocketMode ' "$CLAMD_CONF" \
  && sed -i 's/^LocalSocketMode .*/LocalSocketMode 666/' "$CLAMD_CONF" \
  || echo 'LocalSocketMode 666' >> "$CLAMD_CONF"

grep -q '^FixStaleSocket ' "$CLAMD_CONF" \
  && sed -i 's/^FixStaleSocket .*/FixStaleSocket yes/' "$CLAMD_CONF" \
  || echo 'FixStaleSocket yes' >> "$CLAMD_CONF"

grep -q '^Foreground ' "$CLAMD_CONF" \
  && sed -i 's/^Foreground .*/Foreground false/' "$CLAMD_CONF" \
  || echo 'Foreground false' >> "$CLAMD_CONF"

# In a single Railway container, running clamd as root avoids missing clamav-user problems.
grep -q '^User ' "$CLAMD_CONF" \
  && sed -i 's/^User .*/User root/' "$CLAMD_CONF" \
  || echo 'User root' >> "$CLAMD_CONF"

# Start clamd.
clamd --config-file="$CLAMD_CONF"

# Wait for local socket.
for i in $(seq 1 45); do
  if [ -S /var/run/clamav/clamd.ctl ]; then
    echo "clamd is ready"
    break
  fi

  echo "Waiting for clamd socket..."
  sleep 1
done

if [ ! -S /var/run/clamav/clamd.ctl ]; then
  echo "clamd failed to create socket"
  exit 1
fi

exec uvicorn api.api_v1:app --host 0.0.0.0 --port "${PORT:-8080}"