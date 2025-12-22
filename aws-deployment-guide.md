# AWS Deployment Guide for rss-morning

**Updated for 2025 Best Practices**

This guide covers the end-to-end deployment of `rss-morning` to **AWS Lambda (Container Images)**.

## Documentation Index

The deployment documentation is broken down into specific sections in the `./deployment/` directory:

1.  **[Architecture Overview](./deployment/00_overview.md)**
    *   Learn why we chose AWS Lambda vs Fargate.
    *   See the high-level architecture diagram.

2.  **[Security Principles](./deployment/01_security_principles.md)**
    *   IAM Least Privilege.
    *   Secrets Management (Secrets Manager vs Env Vars).
    *   Network containment.

3.  **[Prerequisites](./deployment/02_prerequisites.md)**
    *   Tools you need installed (AWS CLI v2, CDK, Docker).

4.  **[Application Preparation](./deployment/03_app_preparation.md)**
    *   Preparing the `Dockerfile` for Lambda.
    *   Creating the Lambda Adapter (`lambda_handler`).
    *   Configuring `.dockerignore`.

5.  **[Networking & IAM](./deployment/04_networking_iam.md)**
    *   Choosing between VPC and Public Networking.
    *   Defining the exact IAM permissions required.

6.  **[Infrastructure as Code (CDK)](./deployment/05_infrastructure.md)**
    *   **Python CDK** examples to provision the entire stack.
    *   Deploying the ECR Repo, Lambda Function, and Schedule in one command.

7.  **[Scheduling](./deployment/07_scheduling.md)**
    *   Configuring the EventBridge cron schedule.

---

## Quick Start (Summary)

1.  **Build & Push**:
    ```bash
    aws ecr create-repository --repository-name rss-morning
    docker build -t rss-morning .
    # (Authenticate and Push commands provided in AWS Console/Docs)
    ```

2.  **Deploy Stack**:
    ```bash
    cd infrastructure/
    cdk deploy
    ```

See detailed sections for full instructions.
