# Part 5: Infrastructure as Code (CDK)

We will use **AWS CDK (Python)** to define the stack.

## 1. Setup
Initialize a new CDK app (in a separate folder, e.g., `infrastructure/`):
```bash
mkdir infrastructure && cd infrastructure
cdk init app --language python
source .venv/bin/activate
pip install aws-cdk-lib constructs
```

## 2. The Stack (`infrastructure/infrastructure_stack.py`)

```python
from aws_cdk import (
    Stack,
    Duration,
    aws_lambda as _lambda,
    aws_ecr as ecr,
    aws_events as events,
    aws_events_targets as targets,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
)
from constructs import Construct

class RssMorningStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. ECR Repository (External or Created here)
        # If you already pushed the image manually:
        repo = ecr.Repository.from_repository_name(self, "Repo", "rss-morning")
        
        # 2. Define the Docker Image Function
        docker_func = _lambda.DockerImageFunction(
            self, "RssMorningFunction",
            code=_lambda.DockerImageCode.from_ecr(
                repository=repo,
                tag_or_digest="latest"
            ),
            architecture=_lambda.Architecture.ARM_64, # Cheaper/Faster if using ARM
            timeout=Duration.minutes(15), # Max for Lambda
            memory_size=1024, # 1GB (Adjust based on needs)
            environment={
                "RSS_MORNING_ENV": "prod",
                # "OPENAI_API_KEY": "..." # BETTER: Use Secrets!
            }
        )

        # 3. Grant Permissions to Read Secrets
        # Assuming secret exists with name "rss-morning/openai-key"
        # secret = secretsmanager.Secret.from_secret_name_v2(self, "OpenAIKey", "rss-morning/openai-key")
        # secret.grant_read(docker_func)

        # 4. Schedule (EventBridge)
        # Run every day at 7:00 AM UTC
        rule = events.Rule(
            self, "DailyRunRule",
            schedule=events.Schedule.cron(minute="0", hour="7")
        )
        rule.add_target(targets.LambdaFunction(docker_func))
```

## 3. Deploy
```bash
cdk deploy
```
