# Part 6: Pipeline & Deployment

We are ready to build the artifact and define the running task.

## 1. Build and Push
**Authentication**: Login Docker to AWS ECR.
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
ECR_URL="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_URL
```

**Build & Push**:
```bash
docker build -t rss-morning .
docker tag rss-morning:latest $ECR_URL/rss-morning:latest
docker push $ECR_URL/rss-morning:latest
```

## 2. Register Task Definition
**Why?** This tells ECS *how* to run the container (how much CPU, which image, which secrets).

**Action**: Create `task-def.json`.
```json
{
  "family": "rss-morning",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/RssMorningExecutionRole",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/RssMorningTaskRole",
  "containerDefinitions": [
    {
      "name": "app",
      "image": "ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/rss-morning:latest",
      "essential": true,
      "environment": [
        { "name": "RSS_MORNING_LOG_STDOUT", "value": "1" }
      ],
      "secrets": [
        { "name": "OPENAI_API_KEY", "valueFrom": "/rss-morning/OPENAI_API_KEY" },
        { "name": "RESEND_API_KEY", "valueFrom": "/rss-morning/RESEND_API_KEY" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/rss-morning",
          "awslogs-region": "REGION",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```
*Replace `ACCOUNT_ID` and `REGION` in the file above.*

**Register**:
```bash
aws ecs register-task-definition --cli-input-json file://task-def.json
```
