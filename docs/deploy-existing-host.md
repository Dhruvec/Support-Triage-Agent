# Deploy onto an existing EC2 host (no provisioning)

You already have an **EC2 host**, an **RDS Postgres endpoint**, and an **ElastiCache Redis
endpoint**. This path uses them as-is: it **builds the image on the host** and starts the
`api` + `worker` + `nginx` stack with Docker Compose. No AWS CLI, ECR, S3, SSM, or Terraform
required.

If you instead want a script to create EC2 + RDS + ElastiCache from scratch, use
[`infra/aws/provision.sh`](../infra/aws/provision.sh) via [`docs/deployment-aws.md`](deployment-aws.md).

Two files you will use:

| File | Purpose |
|------|---------|
| [`infra/host/env.example`](../infra/host/env.example) | ready-to-fill `.env` (copy to `.env`) |
| [`infra/host/deploy-on-host.sh`](../infra/host/deploy-on-host.sh) | one-shot build + start + migrate + seed |
| [`docker-compose.host.yml`](../docker-compose.host.yml) | host-build stack (no ECR) |

---

## 0. Copy-paste checklist (the TL;DR)

Run these **on the EC2 host**, from the repo checkout.

```bash
# 1. Get the code onto the host
git clone <your-repo-url> triage && cd triage

# 2. Create and fill the env file (replace every CHANGEME)
cp infra/host/env.example .env
nano .env

# 3. Deploy (builds the image on the host, starts the stack, migrates, seeds)
./infra/host/deploy-on-host.sh

# 4. Verify
curl http://localhost/health
```

Then open `http://<EC2_PUBLIC_IP>/health` in a browser (make sure the host's security group
allows inbound TCP 80).

---

## 1. Prerequisites

### a. Confirm the host can reach RDS and ElastiCache

The app host's **security group** must be allowed inbound on the data stores:

| Target | Port | Rule |
|--------|------|------|
| RDS Postgres | `5432` | allow the **EC2 host's security group** as the source |
| ElastiCache Redis | `6379` | allow the **EC2 host's security group** as the source |

Quick reachability check from the host:

```bash
# Postgres (assumes psql / nc available)
nc -vz <rds-endpoint> 5432
# Redis
nc -vz <elasticache-endpoint> 6379
```

If these hang, fix the security groups before continuing. RDS and ElastiCache live in
subnets; the host must be in a subnet/VPC that can route to them (same VPC is simplest).

### b. Confirm the host user can create the `vector` extension

`init_db()` runs `CREATE EXTENSION IF NOT EXISTS vector`, which only an `rds_superuser`
(the RDS **master user**) may do. Set `DATABASE_URL` to the RDS master user, or pre-create
the extension once:

```sql
-- run once as the master user against the `triage` database
CREATE EXTENSION IF NOT EXISTS vector;
```

### c. Install Docker + the Compose plugin

Amazon Linux 2023:

```bash
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # log out/in for this to take effect
sudo dnf install -y docker-compose-plugin   # provides `docker compose`
```

Ubuntu 22.04/24.04:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # log out/in for this to take effect
```

Verify:

```bash
docker --version && docker compose version
```

> Disk: the build compiles wheels and the image is ~500 MB. Give the host at least a
> **20 GB** volume; `t3.small` (2 GB RAM) is enough to build and run the stack.

---

## 2. Fill in `.env`

```bash
cp infra/host/env.example .env
```

Open `.env` and replace **every** `CHANGEME`. The values that matter most:

| Variable | Set it to |
|----------|-----------|
| `DATABASE_URL` | `postgresql+psycopg://<rds-master-user>:<pw>@<rds-endpoint>:5432/triage?sslmode=require` |
| `REDIS_URL` | `rediss://default:<AUTH_TOKEN>@<elasticache-endpoint>:6379/0` (TLS) or `redis://<endpoint>:6379/0` (no TLS) |
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `MCP_TICKETING_TOKEN` … `MCP_POLICY_TOKEN` | four random values — `openssl rand -hex 16` (repeat 4×) |
| `EVAL_DB_URL` | same as `DATABASE_URL` (durable eval history; do **not** leave the sqlite default) |
| `TRIAGE_API_URL` | leave as `http://api:8000` (compose service name) |

Why each is strict:

- `ENVIRONMENT=production` makes [`mcp_servers/common.py:check_token()`](../mcp_servers/common.py:18)
  **strict** — every `MCP_*_TOKEN` must be non-empty or MCP calls raise `AuthError`.
- `REDIS_URL` **must** use `rediss://` if ElastiCache has in-transit TLS enabled; `arq`'s
  `RedisSettings.from_dsn` reads the scheme and the auth token as the password.
- `TRIAGE_API_URL` must be `http://api:8000` so the worker talks to the API over the compose
  network (the default `http://localhost:8000` would point at the worker container itself).
- `EVAL_DB_URL` defaulting to `sqlite:///./evals_results.db` puts eval history on the
  container filesystem, which is lost on every redeploy.

`.env` is gitignored — never commit it.

---

## 3. Deploy

```bash
./infra/host/deploy-on-host.sh
```

The script runs, in order:

1. `docker compose -f docker-compose.host.yml --env-file .env up -d --build` — builds the
   image on the host and starts `api`, `worker`, `nginx`.
2. Waits for the API, then runs `init_db()` (creates tables + `vector` extension).
3. Runs `python -m apps.api.seed` (idempotent: tenant, KB articles, policies, sample tickets).
4. Prints `docker compose ps`, a health check, and the app URL.

If your user is not in the `docker` group:

```bash
sudo ./infra/host/deploy-on-host.sh
```

### Equivalent manual commands

```bash
docker compose -f docker-compose.host.yml --env-file .env up -d --build
docker compose -f docker-compose.host.yml --env-file .env exec -T api \
  python -c "from apps.api.db import init_db; init_db()"
docker compose -f docker-compose.host.yml --env-file .env exec -T api \
  python -m apps.api.seed
```

---

## 4. Verify

```bash
# from the host
curl -fsS http://localhost/health

# a full ingest round-trip through nginx
curl -fsS -X POST http://localhost/ingest \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"default","subject":"Charged twice","body":"I was billed twice for my order."}'
```

`/health` returns `{"status":"ok","db_ok":true,...}` when RDS is reachable.

From your laptop, open `http://<EC2_PUBLIC_IP>/health` after adding an inbound rule for
TCP 80 on the host's security group.

Useful commands:

```bash
docker compose -f docker-compose.host.yml logs -f api
docker compose -f docker-compose.host.yml logs -f worker
docker compose -f docker-compose.host.yml ps
docker compose -f docker-compose.host.yml down          # stop
docker compose -f docker-compose.host.yml up -d --build # redeploy
```

---

## 5. Updating an existing deployment

```bash
git pull
./infra/host/deploy-on-host.sh
```

`up -d --build` rebuilds only what changed; `init_db()` and `seed` are idempotent, so
re-running is safe.

---

## 6. Security notes

The API ships two **unauthenticated** endpoints that must not be internet-facing:

- `POST /internal/worker` — runs the agent on an arbitrary payload.
- `POST /tickets/{id}/approve` — executes gated write tools.

This stack keeps them off the internet the same way the provisioned stack does:

1. The `api` container publishes **no** host port (`expose: 8000` only).
2. `nginx` is the only public surface and **404s** `/internal/` and `/tickets/{id}/approve`;
   the `/tickets` block is `GET`-only.
3. Data stores accept traffic only from the app host's security group.

For a hardened deployment, add auth to `/internal/worker` and `approve`, terminate TLS at an
ALB, and use a multi-AZ RDS instance — see
[ADR 0009](adr/0009-single-ec2-compose-deployment.md).

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `Missing .env` | run `cp infra/host/env.example .env` first |
| `Docker not installed` | install Docker + Compose plugin (step 1c) |
| `/health` shows `db_ok: false` | `DATABASE_URL` wrong, or the RDS SG doesn't allow the host's SG |
| Worker logs Redis connection errors | `REDIS_URL` not `rediss://` when TLS is on, or wrong AUTH token |
| `AuthError: ... must be set in production` | an `MCP_*_TOKEN` is empty with `ENVIRONMENT=production` |
| `CREATE EXTENSION vector` fails | `DATABASE_URL` user is not the RDS master user |
| Ingest returns 404 from nginx | the route isn't in the allow-list in [`infra/nginx/nginx.conf`](../infra/nginx/nginx.conf) |
| Build fails with no space left | expand the EBS volume (`docker system prune -af` also helps) |
