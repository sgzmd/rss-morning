# Part 1: Security Principles

## 1. Least Privilege (IAM)
*   **Service Role**: The Lambda function must have a devoted IAM Role.
*   **Policies**:
    *   `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` (CloudWatch).
    *   `secretsmanager:GetSecretValue` (for accessing API keys) OR `ssm:GetParameter`.
    *   **NO** broad `*` permissions.

## 2. Secrets Management
*   **Do NOT** embed API keys in `Dockerfile` or source code.
*   **Do NOT** use plain-text Environment Variables in the Lambda configuration for sensitive data (they are visible in the console).
*   **Best Practice**:
    *   Store `OPENAI_API_KEY` and `RESEND_API_KEY` in **AWS Secrets Manager** or **SSM Parameter Store (SecureString)**.
    *   Inject them at runtime:
        *   **Option A**: Use the AWS Parameters and Secrets Lambda Extension (caches secrets, easy access).
        *   **Option B**: Use the AWS SDK (`boto3`) inside the app to fetch them on startup.

## 3. Networking & VPC
*   **Default (Recommended for this use case)**: Run Lambda **outside of VPC**.
    *   **Pros**: Fastest cold starts, free direct internet access (required for RSS feeds).
    *   **Cons**: Cannot access internal private resources (RDS, Redis) - *Not applicable here*.
*   **VPC Deployment**: If you *must* run in a VPC:
    *   Place Lambda in **Private Subnets**.
    *   You **MUST** provision a **NAT Gateway** in a Public Subnet to access public internet (RSS feeds, External APIs).
    *   **Warning**: NAT Gateways cost ~$30/month + data processing fees. Avoid unless necessary.

## 4. Container Security
*   **ReadOnly Root Filesystem**: Configure the application to write temporary files only to `/tmp` (Lambda provides 512MB-10GB ephemeral storage at `/tmp`).
*   **Non-Root User**: Ideally, run the container as a non-root user (though Lambda maps the user automatically, it's good practice in Dockerfile).
