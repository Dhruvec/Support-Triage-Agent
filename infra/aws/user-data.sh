#!/usr/bin/env bash
# ===========================================================================
# EC2 first-boot bootstrap (cloud-init user-data).
#
# provision.sh substitutes __SSM_PARAM__ below before passing this file to
# `aws ec2 run-instances --user-data`. It installs Docker + the Compose plugin,
# pulls the rendered .env from SSM (SecureString), fetches the compose file and
# nginx config from S3, then starts the stack and runs the one-time
# migrate + seed steps.
#
# Logs: /var/log/triage-init.log
# ===========================================================================
set -euxo pipefail

# Mirror all output to a log file for troubleshooting.
exec > >(tee -a /var/log/triage-init.log) 2>&1

APP_DIR=/opt/triage
SSM_PARAM="__SSM_PARAM__"
MARKER="$APP_DIR/.provisioned"

# Idempotency: skip the heavy work if this host already bootstrapped.
if [ -f "$MARKER" ]; then
  echo "Already provisioned; ensuring stack is up."
  cd "$APP_DIR"
  docker compose -f docker-compose.prod.yml --env-file .env up -d || true
  exit 0
fi

# --- Region from IMDSv2 -----------------------------------------------------
TOKEN="$(curl -sX PUT http://169.254.169.254/latest/api/token \
         -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')"
REGION="$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
          http://169.254.169.254/latest/meta-data/placement/region)"

# --- Docker + Compose plugin ------------------------------------------------
dnf install -y docker
systemctl enable --now docker
mkdir -p /usr/libexec/docker/cli-plugins
curl -sSL \
  https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/libexec/docker/cli-plugins/docker-compose
chmod +x /usr/libexec/docker/cli-plugins/docker-compose

# --- Fetch secrets + app artifacts -----------------------------------------
mkdir -p "$APP_DIR/infra/nginx"
aws ssm get-parameter --name "$SSM_PARAM" --with-decryption \
  --region "$REGION" --query Parameter.Value --output text > "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

set -a; . "$APP_DIR/.env"; set +a

aws s3 cp "s3://${TRIAGE_ARTIFACT_BUCKET}/docker-compose.prod.yml" "$APP_DIR/" \
  --region "$REGION"
aws s3 cp "s3://${TRIAGE_ARTIFACT_BUCKET}/nginx/nginx.conf" \
  "$APP_DIR/infra/nginx/nginx.conf" --region "$REGION"

# --- ECR login + start stack ------------------------------------------------
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${TRIAGE_IMAGE%%/*}"

cd "$APP_DIR"
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d

# --- One-time schema + seed (equivalents of `make migrate` / `make seed`) ---
for i in $(seq 1 30); do
  if docker compose -f docker-compose.prod.yml --env-file .env \
       exec -T api python -c "from apps.api.db import init_db; init_db()"; then
    break
  fi
  echo "Waiting for api container ($i)..."; sleep 5
done
docker compose -f docker-compose.prod.yml --env-file .env \
  exec -T api python -m apps.api.seed

touch "$MARKER"
echo "Bootstrap complete."
