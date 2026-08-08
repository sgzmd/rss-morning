# AWS deployment guide

RSS Morning is a finite batch process, so a scheduled ECS Fargate task is a
reasonable deployment shape: EventBridge Scheduler starts the task, ECS pulls the
image from ECR, the task reads configuration from a mounted filesystem, and logs
are collected from stderr by the `awslogs` driver.

This guide describes infrastructure but does not deploy it. Validate the task in a
non-production AWS account before scheduling it.

## Prerequisites

- An AWS account and authenticated AWS CLI
- Docker and permission to create ECR, ECS, EventBridge, IAM, CloudWatch Logs, and
  either EFS or another controlled configuration-delivery mechanism
- A VPC path that permits outbound HTTPS to configured feeds and enabled APIs
- Runtime credentials only for the stages enabled in `config.xml`:
  `GOOGLE_API_KEY`/`GEMINI_API_KEY`, `OPENAI_API_KEY`, and/or `RESEND_API_KEY`

The repository image intentionally excludes private configuration, feeds, prompts,
and environment XML. Do not pass credentials as Docker build arguments or bake
them into an image.

## Runtime files and persistence

The container entry point is `python main.py`; supply the current config-first
argument explicitly:

```text
--config /app/runtime/config.xml
```

Provision `/app/runtime` as a read-only EFS access point containing, at minimum:

- `config.xml`
- its referenced OPML feed file
- its prompt file when summaries are enabled
- its query file when pre-filtering is enabled

All relative paths in `config.xml` resolve from that file's directory. Prefer ECS
secret injection for credentials instead of an environment XML file.

The container filesystem is ephemeral. If database caching is enabled, use a
durable SQLAlchemy connection string such as an appropriately managed database;
do not rely on a container-local SQLite file. FastEmbed can require a model
download and substantial disk/memory. Either provide a persistent writable cache
at `FASTEMBED_CACHE_PATH` or select the OpenAI embedding provider deliberately.

## Build and publish

Create the repository once:

```bash
aws ecr create-repository --repository-name rss-morning --region "$AWS_REGION"
```

Build locally without credentials, then tag and push an immutable version:

```bash
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
VERSION="$(git rev-parse --short HEAD)"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR"
docker build --pull -t "rss-morning:$VERSION" .
docker tag "rss-morning:$VERSION" "$ECR/rss-morning:$VERSION"
docker push "$ECR/rss-morning:$VERSION"
```

Use the immutable `$VERSION` tag in the task definition rather than `latest`.

## ECS task definition

Configure the task with:

- **Launch type:** Fargate
- **Container image:** the immutable ECR image above
- **Command:** `--config`, `/app/runtime/config.xml`
- **Root filesystem:** read-only where compatible with the selected embedding and
  database configuration
- **EFS mount:** runtime configuration at `/app/runtime`, read-only
- **Logging:** `awslogs` to a dedicated CloudWatch Logs group
- **Secrets:** inject only credentials required by enabled stages from Secrets
  Manager or SSM Parameter Store
- **Networking:** no inbound rules; outbound HTTPS only through the chosen public
  IP or NAT design
- **Retries:** keep scheduler retries bounded so API or email failures cannot
  produce an unbounded number of paid task runs

Size CPU, memory, and ephemeral storage from a representative manual run. Do not
assume the local FastEmbed model fits the smallest Fargate task size.

The task role needs access only to its declared secrets, EFS access point, and log
group. The execution role needs the normal ECR pull and log-delivery permissions.

## Validate before scheduling

1. Register a task-definition revision using a non-production email recipient and
   safe API credentials.
2. Run one task manually with the same networking, IAM roles, mounts, command, and
   secrets intended for the schedule.
3. Confirm the task exits successfully, JSON is written to the container log,
   secrets and article bodies are absent from INFO logs, and only the intended
   external calls occurred.
4. Confirm a failed task cannot trigger uncontrolled retries or duplicate mail.

Do not use `--llm-dry-run` as a complete infrastructure test: it prevents the
Gemini request and email delivery by design.

## Schedule and operate

Create an EventBridge Scheduler schedule targeting `ecs:RunTask`. Give the
scheduler role permission to run only the selected task definition and pass only
the task roles it needs. Set the time zone explicitly, configure a bounded retry
policy and dead-letter queue, and initially leave the schedule disabled until the
manual validation above passes.

For updates, build a new immutable image tag, register a new task-definition
revision, manually validate it, and then point the schedule at that revision.
Rotate secrets in Secrets Manager or Parameter Store without rebuilding the image.
