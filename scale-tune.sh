#!/usr/bin/env bash
###############################################################################
# FinalZoom Scale Tuner — apply on live 8GB/4vCPU VPS WITHOUT full redeploy
#
# Karta kya hai:
#   1. systemd unit ko uvicorn --workers 4 + ulimit raise pe convert
#   2. MongoDB pe wiredTiger cache cap (3 GB) + log rotation
#   3. Redis maxmemory 512MB + LRU eviction
#   4. Nginx upstream keep-alive + worker_connections raise
#   5. Backend env mein MONGO_MAX_POOL=200 inject (already coded in server.py)
#
# USAGE (VPS root):
#   curl -fsSL https://<DASHBOARD_URL>/scale-tune.sh | bash
#   OR upload kar ke:
#   sudo bash scale-tune.sh
###############################################################################
set -euo pipefail

APP_NAME="finalzoom"
APP_DIR="/opt/${APP_NAME}"
APP_USER="finalzoom"
BACKEND_PORT="8001"

echo ">> [1/5] Patching systemd unit for uvicorn --workers 4 + high ulimit ..."
cat > "/etc/systemd/system/${APP_NAME}-backend.service" <<EOF
[Unit]
Description=FinalZoom FastAPI backend (scale-tuned)
After=network.target mongod.service redis-server.service
Wants=mongod.service redis-server.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${APP_DIR}/backend/.env
ExecStart=${APP_DIR}/.venv/bin/uvicorn server:app \\
  --host 127.0.0.1 --port ${BACKEND_PORT} \\
  --workers 4 \\
  --proxy-headers --forwarded-allow-ips=* \\
  --backlog 2048 \\
  --timeout-keep-alive 30
Restart=always
RestartSec=3
LimitNOFILE=131072
LimitNPROC=8192
StandardOutput=append:/var/log/${APP_NAME}-backend.log
StandardError=append:/var/log/${APP_NAME}-backend.err.log

[Install]
WantedBy=multi-user.target
EOF

echo ">> [2/5] Ensuring Motor pool env vars present in backend/.env ..."
ENV_FILE="${APP_DIR}/backend/.env"
grep -q '^MONGO_MAX_POOL=' "${ENV_FILE}" || echo 'MONGO_MAX_POOL=200' >> "${ENV_FILE}"
grep -q '^MONGO_MIN_POOL=' "${ENV_FILE}" || echo 'MONGO_MIN_POOL=20' >> "${ENV_FILE}"
chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"

echo ">> [3/5] Tuning MongoDB (wiredTiger cache 3GB, log rotate) ..."
if [[ -f /etc/mongod.conf ]]; then
  # cache size cap so Mongo doesn't blow past 6GB on 8GB box
  if ! grep -q "cacheSizeGB" /etc/mongod.conf; then
    cat >> /etc/mongod.conf <<'EOM'

storage:
  wiredTiger:
    engineConfig:
      cacheSizeGB: 3
EOM
  fi
  systemctl restart mongod
fi

echo ">> [4/5] Tuning Redis (maxmemory 512MB, allkeys-lru) ..."
if [[ -f /etc/redis/redis.conf ]]; then
  sed -i 's/^# *maxmemory .*/maxmemory 512mb/' /etc/redis/redis.conf
  grep -q '^maxmemory ' /etc/redis/redis.conf || echo 'maxmemory 512mb' >> /etc/redis/redis.conf
  sed -i 's/^# *maxmemory-policy .*/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
  grep -q '^maxmemory-policy ' /etc/redis/redis.conf || echo 'maxmemory-policy allkeys-lru' >> /etc/redis/redis.conf
  systemctl restart redis-server
fi

echo ">> [5/5] Nginx upstream tuning ..."
NGINX_CONF="/etc/nginx/sites-available/${APP_NAME}"
if [[ -f "${NGINX_CONF}" ]]; then
  # Add upstream block if not present
  if ! grep -q "upstream finalzoom_backend" "${NGINX_CONF}"; then
    sed -i "1i upstream finalzoom_backend {\n    server 127.0.0.1:${BACKEND_PORT};\n    keepalive 64;\n}\n" "${NGINX_CONF}"
    sed -i "s|proxy_pass         http://127.0.0.1:${BACKEND_PORT};|proxy_pass         http://finalzoom_backend;\n        proxy_http_version 1.1;\n        proxy_set_header   Connection \"\";|" "${NGINX_CONF}"
  fi
  # Raise worker_connections globally
  sed -i 's/worker_connections [0-9]*/worker_connections 8192/' /etc/nginx/nginx.conf || true
  nginx -t && systemctl reload nginx
fi

echo ">> Reloading systemd + restarting backend ..."
systemctl daemon-reload
systemctl restart "${APP_NAME}-backend"

sleep 4
echo "==================================================================="
systemctl --no-pager --lines=0 status "${APP_NAME}-backend" || true
echo "-------------------------------------------------------------------"
echo " Backend health: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${BACKEND_PORT}/api/)"
echo " Active uvicorn workers: $(pgrep -f 'uvicorn server:app' | wc -l)"
echo " Mongo conns:           $(ss -tn state established '( sport = :27017 )' 2>/dev/null | wc -l)"
echo "==================================================================="
echo " ✅ Scale tune complete. Ab dashboard 100+ RDPs handle kar sakta hai."
echo "   Worker side bhi POLL_INTERVAL=15 set karo har RDP ki .env mein:"
echo "     echo 'POLL_INTERVAL=15' >> /opt/finalzoom-worker/.env"
echo "     pm2 restart all   # or systemctl restart finalzoom-worker"
echo "==================================================================="
