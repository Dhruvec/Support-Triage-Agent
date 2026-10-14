#!/usr/bin/env bash
# ===========================================================================
# Cheapest-viable AWS deployment for the triage agent.
#
# Creates, via the AWS CLI only (no Terraform/CDK):
#   * S3 bucket          - hosts docker-compose.prod.yml + nginx.conf
#   * ECR repository     - hosts the container image
#   * Security groups    - ec2 (public :80) / rds (:5432) / redis (:6379)
#   * RDS Postgres 16    - pgvector enabled by init_db() at first boot
#   * ElastiCache Redis 7 - in-transit TLS + AUTH token (rediss://)
#   * IAM role+profile   - ECR read, SSM read, SSM RunCommand, S3 read
#   * EC2 instance       - runs docker-compose.prod.yml via infra/aws/user-data.sh
#   * SSM SecureString   - the rendered .env (secrets never touch S3)
#
# Usage:
#   AWS_REGION=us-east-1 ./infra/aws/provision.sh up      # create everything
#   ./infra/aws/provision.sh down                          # tear everything down
#
# Overridable env vars: AWS_REGION, PROJECT, DB_INSTANCE_CLASS,
#   CACHE_NODE_TYPE, EC2_INSTANCE_TYPE, KEY_NAME, ALLOWED_CIDR.
# ===========================================================================
set -euo pipefail

AWS_REGION=${AWS_REGION:-us-east-1}
PROJECT=${PROJECT:-triage}
DB_INSTANCE_CLASS=${DB_INSTANCE_CLASS:-db.t4g.micro}
CACHE_NODE_TYPE=${CACHE_NODE_TYPE:-cache.t4g.micro}
PG_ENGINE_VERSION=${PG_ENGINE_VERSION:-16}
EC2_INSTANCE_TYPE=${EC2_INSTANCE_TYPE:-t3.small}
KEY_NAME=${KEY_NAME:-}
# Restrict SSH/HTTP if you like; defaults to the whole internet for the demo.
ALLOWED_CIDR=${ALLOWED_CIDR:-0.0.0.0/0}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUTS="$SCRIPT_DIR/.outputs.env"
ENV_TMP="$(mktemp)"
USERDATA_TMP="$(mktemp)"
trap 'rm -f "$ENV_TMP" "$USERDATA_TMP"' EXIT

SSM_PARAM="/${PROJECT}/env"
DB_ID="${PROJECT}-db"
CACHE_ID="${PROJECT}-cache"
ECR_REPO="${PROJECT}-agent"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }

aws() { command aws --region "$AWS_REGION" "$@"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
preflight() {
  command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
  command -v docker >/dev/null || { echo "docker not found (needed to build/push)" >&2; exit 1; }
  AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
            --query 'Vpcs[0].VpcId' --output text)"
  if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
    echo "No default VPC in $AWS_REGION. Create one or adapt this script to your VPC." >&2
    exit 1
  fi
  SUBNET_IDS="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
                --query 'Subnets[].SubnetId' --output text | tr '\t' ' ')"
  BUCKET="${PROJECT}-artifacts-${AWS_ACCOUNT_ID}-${AWS_REGION}"
  REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  IMAGE_URI="${REGISTRY}/${ECR_REPO}:latest"
  # Reuse secrets across runs so a re-run never rotates the RDS password or the
  # Redis auth token out from under an already-provisioned cluster.
  SECRETS="$SCRIPT_DIR/.secrets.env"
  # shellcheck disable=SC1090
  [ -f "$SECRETS" ] && . "$SECRETS"
  DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 16)}"
  REDIS_AUTH_TOKEN="${REDIS_AUTH_TOKEN:-$(openssl rand -hex 24)}"
  (umask 077 && cat > "$SECRETS" <<EOF
DB_PASSWORD=${DB_PASSWORD}
REDIS_AUTH_TOKEN=${REDIS_AUTH_TOKEN}
EOF
  )
  log "account=$AWS_ACCOUNT_ID region=$AWS_REGION vpc=$VPC_ID"
}

# ---------------------------------------------------------------------------
# Infrastructure pieces
# ---------------------------------------------------------------------------
create_bucket() {
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    log "S3 bucket $BUCKET already exists"; return
  fi
  log "Creating S3 bucket $BUCKET"
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
}

create_ecr() {
  if aws ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1; then
    log "ECR repo $ECR_REPO already exists"; return
  fi
  log "Creating ECR repo $ECR_REPO"
  aws ecr create-repository --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true >/dev/null
}

sg_id_by_name() {
  aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$1" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true
}

ensure_sg() { # $1=name $2=description
  local id; id="$(sg_id_by_name "$1")"
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    id="$(aws ec2 create-security-group --group-name "$1" \
          --description "$2" --vpc-id "$VPC_ID" \
          --query GroupId --output text)"
  fi
  printf '%s' "$id"
}

create_sgs() {
  log "Ensuring security groups"
  EC2_SG="$(ensure_sg "${PROJECT}-ec2-sg" "triage app host")"
  RDS_SG="$(ensure_sg "${PROJECT}-rds-sg" "triage RDS")"
  REDIS_SG="$(ensure_sg "${PROJECT}-redis-sg" "triage ElastiCache")"

  aws ec2 authorize-security-group-ingress --group-id "$EC2_SG" \
    --ip-permissions "[{\"IpProtocol\":\"tcp\",\"FromPort\":80,\"ToPort\":80,\"IpRanges\":[{\"CidrIp\":\"$ALLOWED_CIDR\"}]}]" \
    >/dev/null 2>&1 || true
  if [ -n "$KEY_NAME" ]; then
    aws ec2 authorize-security-group-ingress --group-id "$EC2_SG" \
      --protocol tcp --port 22 --cidr "$ALLOWED_CIDR" >/dev/null 2>&1 || true
  fi
  # Data stores accept traffic ONLY from the app host's security group.
  aws ec2 authorize-security-group-ingress --group-id "$RDS_SG" \
    --protocol tcp --port 5432 --source-group "$EC2_SG" >/dev/null 2>&1 || true
  aws ec2 authorize-security-group-ingress --group-id "$REDIS_SG" \
    --protocol tcp --port 6379 --source-group "$EC2_SG" >/dev/null 2>&1 || true
}

create_rds() {
  if aws rds describe-db-instances --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then
    log "RDS $DB_ID already exists"
  else
    log "Creating RDS Postgres 16 ($DB_INSTANCE_CLASS) - takes several minutes"
    aws rds create-db-instance \
      --db-instance-identifier "$DB_ID" \
      --db-instance-class "$DB_INSTANCE_CLASS" \
      --engine postgres --engine-version "${PG_ENGINE_VERSION:-16}" \
      --master-username triage --master-user-password "$DB_PASSWORD" \
      --db-name triage \
      --allocated-storage 20 --storage-type gp2 \
      --no-multi-az --backup-retention-period 1 \
      --vpc-security-group-ids "$RDS_SG" --no-publicly-accessible >/dev/null
  fi
  log "Waiting for RDS to become available"
  aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
  DB_HOST="$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" \
             --query 'DBInstances[0].Endpoint.Address' --output text)"
}

create_cache() {
  if aws elasticache describe-replication-groups \
       --replication-group-id "$CACHE_ID" >/dev/null 2>&1; then
    log "ElastiCache $CACHE_ID already exists"
  else
    log "Creating ElastiCache Redis 7 with TLS + AUTH ($CACHE_NODE_TYPE)"
    aws elasticache create-replication-group \
      --replication-group-id "$CACHE_ID" \
      --replication-group-description "triage cache" \
      --engine redis --engine-version 7.1 \
      --cache-node-type "$CACHE_NODE_TYPE" \
      --num-node-groups 1 --replicas-per-node-group 0 \
      --transit-encryption-enabled \
      --auth-token "$REDIS_AUTH_TOKEN" \
      --security-group-ids "$REDIS_SG" >/dev/null
  fi
  log "Waiting for ElastiCache to become available"
  aws elasticache wait replication-group-available --replication-group-id "$CACHE_ID"
  CACHE_HOST="$(aws elasticache describe-replication-groups \
                --replication-group-id "$CACHE_ID" \
                --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' \
                --output text)"
  if [ -z "$REDIS_AUTH_TOKEN" ]; then
    warn "REDIS_AUTH_TOKEN empty; set it to the group's auth token before use."
  fi
}

create_iam() {
  ROLE="${PROJECT}-ec2-role"
  PROFILE="${PROJECT}-ec2-profile"
  log "Ensuring IAM role/profile $ROLE"
  if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
      >/dev/null
    aws iam attach-role-policy --role-name "$ROLE" \
      --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
    aws iam attach-role-policy --role-name "$ROLE" \
      --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
    aws iam put-role-policy --role-name "$ROLE" --policy-name "${PROJECT}-inline" \
      --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
        {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:${AWS_REGION}:${AWS_ACCOUNT_ID}:parameter${SSM_PARAM}\"},
        {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\"],\"Resource\":\"arn:aws:s3:::${BUCKET}/*\"}]}"
  fi
  if ! aws iam get-instance-profile --instance-profile-name "$PROFILE" >/dev/null 2>&1; then
    aws iam create-instance-profile --instance-profile-name "$PROFILE" >/dev/null
    aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE" \
      --role-name "$ROLE"
    sleep 10   # let IAM propagate before EC2 attach
  fi
  PROFILE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:instance-profile/${PROFILE}"
}

build_and_push() {
  log "Building and pushing image to ECR"
  aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  docker build -t "$IMAGE_URI" "$REPO_ROOT"
  docker push "$IMAGE_URI" >/dev/null
}

render_env() {
  log "Rendering .env (secrets -> SSM SecureString, never S3)"
  cat > "$ENV_TMP" <<EOF
TRIAGE_IMAGE=${IMAGE_URI}
AWS_REGION=${AWS_REGION}
TRIAGE_ARTIFACT_BUCKET=${BUCKET}
ENVIRONMENT=production
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://triage:${DB_PASSWORD}@${DB_HOST}:5432/triage?sslmode=require
REDIS_URL=rediss://default:${REDIS_AUTH_TOKEN}@${CACHE_HOST}:6379/0
TRIAGE_API_URL=http://api:8000
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
MODEL_CLASSIFY=claude-3-5-haiku-latest
MODEL_DECIDE=claude-3-5-sonnet-latest
MODEL_DRAFT=claude-3-5-sonnet-latest
MODEL_JUDGE=claude-3-5-sonnet-latest
EMBEDDING_MODEL=text-embedding-3-small
MCP_TRANSPORT=stdio
MCP_TICKETING_URL=http://ticketing-mcp:8801/mcp
MCP_KB_URL=http://kb-mcp:8802/mcp
MCP_CUSTOMER_URL=http://customer-mcp:8803/mcp
MCP_POLICY_URL=http://policy-mcp:8804/mcp
MCP_TICKETING_TOKEN=$(openssl rand -hex 16)
MCP_KB_TOKEN=$(openssl rand -hex 16)
MCP_CUSTOMER_TOKEN=$(openssl rand -hex 16)
MCP_POLICY_TOKEN=$(openssl rand -hex 16)
CONFIDENCE_ESCALATE_THRESHOLD=0.65
EVAL_DB_URL=postgresql+psycopg://triage:${DB_PASSWORD}@${DB_HOST}:5432/triage?sslmode=require
JUDGE_RUBRIC_VERSION=v1
EOF
  aws ssm put-parameter --name "$SSM_PARAM" --type SecureString \
    --value "file://$ENV_TMP" --overwrite >/dev/null

  log "Uploading compose + nginx config to s3://$BUCKET"
  aws s3 cp "$REPO_ROOT/docker-compose.prod.yml" "s3://$BUCKET/docker-compose.prod.yml" >/dev/null
  aws s3 cp "$REPO_ROOT/infra/nginx/nginx.conf" "s3://$BUCKET/nginx/nginx.conf" >/dev/null
}

launch_ec2() {
  local ami
  ami="$(aws ssm get-parameter \
        --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
        --query 'Parameter.Value' --output text)"
  sed "s#__SSM_PARAM__#${SSM_PARAM}#g" "$SCRIPT_DIR/user-data.sh" > "$USERDATA_TMP"

  local key_args=()
  [ -n "$KEY_NAME" ] && key_args=(--key-name "$KEY_NAME")

  log "Launching EC2 ($EC2_INSTANCE_TYPE)"
  EC2_INSTANCE_ID="$(aws ec2 run-instances \
    --image-id "$ami" --instance-type "$EC2_INSTANCE_TYPE" \
    --iam-instance-profile "Arn=${PROFILE_ARN}" \
    --security-group-ids "$EC2_SG" "${key_args[@]}" \
    --user-data "file://$USERDATA_TMP" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${PROJECT}-host}]" \
    --query 'Instances[0].InstanceId' --output text)"
  log "Waiting for EC2 $EC2_INSTANCE_ID"
  aws ec2 wait instance-running --instance-ids "$EC2_INSTANCE_ID"
  PUBLIC_IP="$(aws ec2 describe-instances --instance-ids "$EC2_INSTANCE_ID" \
               --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
}

write_outputs() {
  cat > "$OUTPUTS" <<EOF
AWS_REGION=${AWS_REGION}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID}
PROJECT=${PROJECT}
BUCKET=${BUCKET}
ECR_REPO=${ECR_REPO}
IMAGE_URI=${IMAGE_URI}
INSTANCE_ID=${EC2_INSTANCE_ID}
PUBLIC_IP=${PUBLIC_IP}
SSM_PARAM=${SSM_PARAM}
DB_ID=${DB_ID}
CACHE_ID=${CACHE_ID}
EC2_SG=${EC2_SG}
RDS_SG=${RDS_SG}
REDIS_SG=${REDIS_SG}
EOF
}

# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------
cmd_down() {
  AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  [ -f "$OUTPUTS" ] && . "$OUTPUTS"

  log "Deleting EC2 $INSTANCE_ID"
  [ -n "${INSTANCE_ID:-}" ] && aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 || true
  [ -n "${INSTANCE_ID:-}" ] && aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID" 2>/dev/null || true

  log "Deleting RDS $DB_ID (no final snapshot)"
  aws rds delete-db-instance --db-instance-identifier "$DB_ID" \
    --skip-final-snapshot --delete-automated-backups >/dev/null 2>&1 || true

  log "Deleting ElastiCache $CACHE_ID"
  aws elasticache delete-replication-group --replication-group-id "$CACHE_ID" >/dev/null 2>&1 || true

  log "Deleting ECR repo, SSM param, S3 bucket, security groups, IAM"
  aws ecr delete-repository --repository-name "$ECR_REPO" --force >/dev/null 2>&1 || true
  aws ssm delete-parameter --name "$SSM_PARAM" >/dev/null 2>&1 || true
  aws s3 rm "s3://$BUCKET" --recursive >/dev/null 2>&1 || true
  aws s3api delete-bucket --bucket "$BUCKET" >/dev/null 2>&1 || true
  for sg in "${EC2_SG:-}" "${RDS_SG:-}" "${REDIS_SG:-}"; do
    [ -n "$sg" ] && aws ec2 delete-security-group --group-id "$sg" >/dev/null 2>&1 || true
  done
  aws iam remove-role-from-instance-profile \
    --instance-profile-name "${PROJECT}-ec2-profile" --role-name "${PROJECT}-ec2-role" >/dev/null 2>&1 || true
  aws iam delete-instance-profile --instance-profile-name "${PROJECT}-ec2-profile" >/dev/null 2>&1 || true
  aws iam delete-role-policy --role-name "${PROJECT}-ec2-role" --policy-name "${PROJECT}-inline" >/dev/null 2>&1 || true
  for p in AmazonEC2ContainerRegistryReadOnly AmazonSSMManagedInstanceCore; do
    aws iam detach-role-policy --role-name "${PROJECT}-ec2-role" \
      --policy-arn "arn:aws:iam::aws:policy/$p" >/dev/null 2>&1 || true
  done
  aws iam delete-role --role-name "${PROJECT}-ec2-role" >/dev/null 2>&1 || true
  rm -f "$OUTPUTS"
  log "Teardown complete."
}

# ---------------------------------------------------------------------------
cmd_up() {
  preflight
  create_bucket
  create_ecr
  create_sgs
  create_rds
  create_cache
  create_iam
  build_and_push
  render_env
  launch_ec2
  write_outputs

  cat <<EOF

$(log "Deployment complete")
  App URL      : http://${PUBLIC_IP}/health
  Ingest       : curl -X POST http://${PUBLIC_IP}/ingest -H 'Content-Type: application/json' -d '{...}'
  Instance id  : ${EC2_INSTANCE_ID}   (log: sudo tail -f /var/log/triage-init.log)
  Outputs file : ${OUTPUTS}
  Redeploy     : ./infra/aws/deploy.sh

  NOTE: it can take ~1-2 min after boot for the containers to be healthy.
  The unauthenticated /internal/worker and /tickets/{id}/approve routes are
  blocked at nginx and are NOT reachable from the internet.
EOF
}

case "${1:-up}" in
  up)   cmd_up ;;
  down) cmd_down ;;
  *)    echo "usage: $0 [up|down]" >&2; exit 2 ;;
esac
