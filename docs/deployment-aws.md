# Deploying to AWS (cheapest viable path)

A single-EC2 host running Docker Compose, with **managed** Postgres (RDS) and Redis
(ElastiCache). No VPC, ALB, ECS or Fargate — the goal is the smallest bill that runs the
real architecture from [ADR 0007](adr/0007-stateless-worker-ships-jobs-to-api.md).

Rationale for choosing the EC2 host over App Runner: the system runs a **long-lived `arq`
worker** (`apps/worker/main.py`) alongside the API, plus four MCP processes. App Runner only
runs HTTP request/response services and cannot host a persistent queue consumer or the MCP
processes, so it cannot run this workload without reintroducing an ECS worker. A single EC2
host runs the existing `docker-compose.yml` shape almost verbatim. See
[ADR 0009](adr/0009-single-ec2-compose-deployment.md).

## What gets created

| Piece | Service | Notes |
|-------|---------|-------|
| App host | EC2 `t3.small`, Amazon Linux 2023 | runs `api` + `worker` + `nginx` |
| Database | RDS Postgres 16 (`db.t4g.micro`) | `init_db()` runs `CREATE EXTENSION vector` |
| Cache/queue | ElastiCache Redis 7 (`cache.t4g.micro`) | in-transit TLS + AUTH token |
| Images | ECR | one repo, one image, two entrypoints |
| Secrets | SSM Parameter Store (SecureString) | the rendered `.env` |
| Config | S3 (private) | `docker-compose.prod.yml` + `nginx.conf` |
| Access | IAM instance profile + SSM RunCommand | no SSH required for deploys |

Security groups scope the data stores to the app host's SG only. The API container publishes
**no** host port; `nginx` is the only public surface.

## Prerequisites

- AWS CLI v2 authenticated with permission to create the resources above.
- Docker (for building/pushing the image).
- A default VPC in the target region (the script uses it and reads its subnet IDs).
- Optionally an EC2 key pair (`KEY_NAME=...`) if you want SSH.

## One-command deploy

```bash
AWS_REGION=us-east-1 ANTHROPIC_API_KEY=sk-ant-... ./infra/aws/provision.sh up
```

This runs, in order: S3 bucket → ECR repo → security groups → RDS → ElastiCache → IAM →
build & push image → render `.env` into SSM → launch EC2. RDS and ElastiCache take several
minutes each. On completion it writes `infra/aws/.outputs.env` (gitignored) and prints the
app URL. First boot then runs `infra/aws/user-data.sh`, which installs Docker, pulls the
`.env`, starts the stack, and runs the one-time `migrate` + `seed`.

Verify:

```bash
curl http://<PUBLIC_IP>/health
sudo tail -f /var/log/triage-init.log     # on the host, via SSM Session Manager
```

## Redeploying after a code change

```bash
./infra/aws/deploy.sh            # tags with the short git SHA
./infra/aws/deploy.sh v1.2.3     # or an explicit tag
```

Builds, pushes, updates the compose/nginx config in S3, and restarts the stack over SSM
RunCommand (no SSH).

## Manual / from-source deploy

> If you **already have** EC2 + RDS + ElastiCache and want to skip provisioning entirely,
> the dedicated host-build path is easier:
> [`docs/deploy-existing-host.md`](deploy-existing-host.md) (uses `docker-compose.host.yml`
> and `infra/host/deploy-on-host.sh`).

If you'd rather not run `provision.sh` (e.g. you already have RDS + Redis) but want to
build off-host and push to your own registry:

1. Build and push the image to your own registry:

   ```bash
   docker build -t <registry>/triage-agent:latest .
   docker push <registry>/triage-agent:latest
   ```

2. On the host, place `docker-compose.prod.yml` and `infra/nginx/nginx.conf`, then create
   `.env` from [`.env.production.example`](../.env.production.example) and fill it in
   (set `TRIAGE_IMAGE`, `DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`, and the four
   `MCP_*_TOKEN`s).

3. Start it:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env up -d
   docker compose -f docker-compose.prod.yml --env-file .env exec api \
     python -c "from apps.api.db import init_db; init_db()"
   docker compose -f docker-compose.prod.yml --env-file .env exec api \
     python -m apps.api.seed
   ```

## Environment notes (things that bite)

- **`ENVIRONMENT=production`** makes `mcp_servers/common.py:check_token()` **strict** — every
  `MCP_*_TOKEN` must be set or MCP calls raise `AuthError`. `provision.sh` generates them.
- **`REDIS_URL` must use `rediss://`** because the ElastiCache group enables in-transit TLS;
  `arq`'s `RedisSettings.from_dsn` honours the TLS scheme and the auth token is the password.
- **`EVAL_DB_URL` must be durable.** The default `sqlite:///./evals_results.db` lives on the
  container's ephemeral filesystem and loses eval history on redeploy. Point it at RDS.
- **`TRIAGE_API_URL`** must be `http://api:8000` so the worker calls the API over the compose
  network (the default is `http://localhost:8000`).
- **RDS `vector` extension.** `init_db()` issues `CREATE EXTENSION IF NOT EXISTS vector`, so
  the connecting user must be the RDS master user (`rds_superuser`). Postgres 16 on RDS ships
  pgvector.
- **Connection budget.** `apps/api/db.py` sets `pool_size=10, max_overflow=20`. Keep RDS
  `max_connections` above the API pool (a `db.t4g.micro` allows ~85). One API process here,
  so the default is fine.

## Security posture

The API ships two **unauthenticated** endpoints that must not be internet-facing:

- `POST /internal/worker` — accepts arbitrary payloads and runs the agent.
- `POST /tickets/{id}/approve` — executes gated write tools.

Defenses applied in this deployment:

1. The `api` container publishes no host ports.
2. `nginx` (the only public surface) **404s** `/internal/` and `/tickets/{id}/approve`, and
   the `/tickets` block is `GET`-only.
3. Data stores accept traffic only from the app host's security group.

Follow-ups for a hardened deployment (see [ADR 0009](adr/0009-single-ec2-compose-deployment.md)):
add a shared-secret or IAM-auth header to `/internal/worker`, require auth on `approve`, put
TLS/ALB in front, and move to a multi-AZ RDS instance.

## Cost sketch (us-east-1, approximate, on-demand)

- EC2 `t3.small`: ~$15/mo
- RDS `db.t4g.micro` (20 GiB gp2): ~$13–15/mo
- ElastiCache `cache.t4g.micro`: ~$12–13/mo
- ECR/S3/SSM: a few cents

Roughly **$40–45/mo**. Stop the EC2 instance when idle, and note RDS/ElastiCache keep
billing while running — `./infra/aws/provision.sh down` tears everything down.

## Teardown

```bash
./infra/aws/provision.sh down
```

Deletes EC2, RDS (no final snapshot), ElastiCache, ECR, the SSM parameter, the S3 bucket,
security groups, and IAM. Adjust if you want a final DB snapshot.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `/health` shows `db_ok: false` | `DATABASE_URL` wrong, or RDS SG missing the app SG |
| Worker logs connection errors to Redis | `REDIS_URL` not `rediss://`, or wrong auth token |
| `AuthError: ... must be set in production` | an `MCP_*_TOKEN` is unset with `ENVIRONMENT=production` |
| Ingest returns 404 from nginx | route not in the allow-list in `infra/nginx/nginx.conf` |
| `CREATE EXTENSION vector` fails | `DATABASE_URL` user is not the RDS master user |
