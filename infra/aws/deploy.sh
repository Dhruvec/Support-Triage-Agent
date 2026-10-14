#!/usr/bin/env bash
# ===========================================================================
# Redeploy the triage agent on the running EC2 host.
#
# Builds a new image from the local working tree, pushes it to ECR, uploads the
# updated compose/nginx config, and restarts the stack over SSM RunCommand
# (no SSH key or public SSH port required).
#
# Usage: ./infra/aws/deploy.sh
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUTS="$SCRIPT_DIR/.outputs.env"

[ -f "$OUTPUTS" ] || { echo "Run provision.sh first ($OUTPUTS missing)." >&2; exit 1; }
# shellcheck disable=SC1090
. "$OUTPUTS"

TAG="${1:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo latest)}"
TAG="$(echo "$TAG" | tr -cd 'A-Za-z0-9._-')"
IMAGE_TAGGED="${IMAGE_URI%:*}:${TAG}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

log "Building image ${IMAGE_TAGGED}"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${IMAGE_URI%%/*}" >/dev/null
docker build -t "$IMAGE_TAGGED" -t "$IMAGE_URI" "$REPO_ROOT"
docker push "$IMAGE_TAGGED"
docker push "$IMAGE_URI"

log "Uploading compose + nginx config"
aws s3 cp "$REPO_ROOT/docker-compose.prod.yml" \
  "s3://${BUCKET}/docker-compose.prod.yml" --region "$AWS_REGION" >/dev/null
aws s3 cp "$REPO_ROOT/infra/nginx/nginx.conf" \
  "s3://${BUCKET}/nginx/nginx.conf" --region "$AWS_REGION" >/dev/null

log "Restarting stack on $INSTANCE_ID via SSM"
CMD_ID="$(aws ssm send-command \
  --region "$AWS_REGION" \
  --document-name "AWS-RunShellScript" \
  --instance-ids "$INSTANCE_ID" \
  --comment "triage redeploy ${TAG}" \
  --parameters 'commands=[
    "set -e",
    "cd /opt/triage",
    "aws s3 cp s3://'"${BUCKET}"'/docker-compose.prod.yml ./ --region '"${AWS_REGION}"'",
    "aws s3 cp s3://'"${BUCKET}"'/nginx/nginx.conf ./infra/nginx/nginx.conf --region '"${AWS_REGION}"'",
    "aws ecr get-login-password --region '"${AWS_REGION}"' | docker login --username AWS --password-stdin '"${IMAGE_URI%%/*}"'",
    "docker compose -f docker-compose.prod.yml --env-file .env pull",
    "docker compose -f docker-compose.prod.yml --env-file .env up -d",
    "docker image prune -f"
  ]' \
  --query 'Command.CommandId' --output text)"

log "Waiting for command $CMD_ID"
for _ in $(seq 1 60); do
  status="$(aws ssm list-command-invocations --region "$AWS_REGION" \
            --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
            --query 'CommandInvocations[0].Status' --output text)"
  case "$status" in
    Success) log "Redeploy complete: http://${PUBLIC_IP}/health"; exit 0 ;;
    Failed|TimedOut|Cancelled) echo "Redeploy failed ($status)." >&2
      aws ssm list-command-invocations --region "$AWS_REGION" \
        --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --details \
        --query 'CommandInvocations[0].CommandPlugins[0].Output' --output text >&2
      exit 1 ;;
  esac
  sleep 5
done
echo "Timed out waiting for SSM command." >&2; exit 1
