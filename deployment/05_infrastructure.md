# Part 5: Infrastructure (ECR & Secrets)

Now we engage the storage and configuration services.

## 1. Amazon ECR (Elastic Container Registry)
**Why?** We need a place to host our Docker image that ECS can pull from securely.
**Action**:
```bash
aws ecr create-repository --repository-name rss-morning
```

## 2. CloudWatch Logs
**Why?** ECS can auto-create log groups, but creating one explicitly lets us control retention (cost management).
**Action**:
```bash
aws logs create-log-group --log-group-name /ecs/rss-morning
aws logs put-retention-policy --log-group-name /ecs/rss-morning --retention-in-days 14
```

## 3. Secrets Management (SSM Parameter Store)
**Why?** We never deploy `.env` files. We store secrets in SSM as `SecureString` types. Fargate will decrypt them at start time.

**Action**:
```bash
# OpenAI Key
aws ssm put-parameter \
    --name "/rss-morning/OPENAI_API_KEY" \
    --value "sk-YOUR_KEY_HERE" \
    --type "SecureString"

# Resend/Sendgrid Key
aws ssm put-parameter \
    --name "/rss-morning/RESEND_API_KEY" \
    --value "re_YOUR_KEY_HERE" \
    --type "SecureString"
```
*Note: Replace `YOUR_KEY_HERE` with actual values.*
