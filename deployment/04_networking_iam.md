# Part 4: Networking & ID

## 1. Networking Strategy

### Option A: Public Networking (Recommended)
*   **Configuration**: Configure the Lambda function with **No VPC**.
*   **Behavior**: The Lambda functions runs in AWS's public secure zone. It has direct access to the public internet.
*   **Cost**: $0 extra.
*   **Use Case**: Perfect for `rss-morning` which only needs to access public RSS feeds, OpenAI API, and Resend API.

### Option B: VPC Networking
*   **Configuration**: Connect Lambda to a VPC Private Subnet.
*   **Requirement**: You **MUST** deploy a **NAT Gateway** in a Public Subnet and route traffic through it.
*   **Cost**: ~$30/month minimum for NAT Gateway.
*   **Use Case**: Only required if you need to connect to a private RDS database or ElastiCache.

**Decision**: Go with **Option A** (No VPC).

## 2. IAM Roles (Identity)

### Lambda Execution Role
This role assumes `lambda.amazonaws.com` service principal and needs the following permissions:

**1. Basic Execution (Logs)**
Managed Policy: `AWSLambdaBasicExecutionRole`
*   `logs:CreateLogGroup`
*   `logs:CreateLogStream`
*   `logs:PutLogEvents`

**2. Secrets Access (If using Secrets Manager)**
Inline Policy:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "secretsmanager:GetSecretValue",
            "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:rss-morning/*"
        }
    ]
}
```

**3. SSM Parameter Access (If using Parameter Store)**
Inline Policy:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "ssm:GetParameter",
            "Resource": "arn:aws:ssm:REGION:ACCOUNT:parameter/rss-morning/*"
        }
    ]
}
```
