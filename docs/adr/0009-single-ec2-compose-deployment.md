# ADR 0009 — Single-EC2 Compose deployment, managed data stores

**Status:** Accepted

## Context

The system must be deployed on AWS at the lowest viable cost. It runs an HTTP API
(`apps/api`), a long-lived `arq` queue consumer (`apps/worker`), and four MCP processes, and
it needs Postgres with `pgvector` plus Redis. The repository already ships a working
`Dockerfile` and `docker-compose.yml`, and explicitly prefers a stateless worker that ships
jobs to the API runner ([ADR 0007](0007-stateless-worker-ships-jobs-to-api.md)).

Two candidate cheap paths were considered: AWS App Runner, and a single EC2 host running
Docker Compose.

## Decision

Deploy **one EC2 instance running Docker Compose** (`docker-compose.prod.yml`), backed by
**managed RDS Postgres** and **managed ElastiCache Redis**. Provide minimal AWS-CLI scripts
(`infra/aws/provision.sh`, `deploy.sh`, `user-data.sh`) rather than a full VPC/IaC stack.
An `nginx` sidecar is the only public surface.

App Runner was rejected because it only runs request/response HTTP services: it cannot host
the persistent `arq` worker or the MCP processes, so using it would force an ECS worker
anyway — more moving parts, not fewer.

## Consequences

- The production topology mirrors the existing compose file almost verbatim; very little new
  code, and the same image serves both the API and the worker.
- Cost is dominated by the two managed stores plus one small instance (~$40–45/mo), which is
  the cheapest way to keep the *real* architecture (queue + worker + pgvector).
- The single host is a **single point of failure** and cannot scale horizontally. Acceptable
  for the current stage; a move to ECS/ALB is the natural next step.
- Data is durable even though compute is not: RDS and ElastiCache are managed, and the eval
  DB is repointed from the default ephemeral SQLite to Postgres.
- Secrets are stored as an SSM SecureString and rendered to the host at boot; the tenant
  image is pulled from ECR with an instance role. No long-lived keys on disk beyond `.env`.

## Alternatives

- **App Runner + external worker** — cannot run the worker; rejected.
- **ECS Fargate + ALB + NAT/VPC endpoints** — robust and horizontally scalable, but the NAT
  gateway and ALB add cost and complexity that this stage does not need.
- **Single EC2 with Postgres/Redis as containers** — cheapest on paper, but state on the
  instance disk is fragile and harder to back up; managed stores are worth the small premium.
- **Terraform/CDK** — more reproducible, but heavier than the "minimal scripts" constraint
  and not required to stand this up.
