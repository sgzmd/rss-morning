# Part 2: Prerequisites

Before deploying, ensure you have the following installed and configured:

## 1. AWS CLI v2
*   **Install**: [Official AWS CLI Install Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
*   **Verify**: `aws --version`
*   **Configure**: Run `aws configure` to set up your credentials and default region.

## 2. Docker
*   **Install**: Docker Desktop or Docker Engine.
*   **Verify**: `docker --version`
*   **Purpose**: Used to build the container image locally before pushing to ECR.

## 3. AWS CDK (Cloud Development Kit) - Python
We will use AWS CDK to define our infrastructure as code.
*   **Install Node.js** (Required for CDK CLI): `npm install -g aws-cdk`
*   **Verify**: `cdk --version`
*   **Python Dependencies**: You will need to install python CDK libraries in your project (listed in infrastructure section).

## 4. Accounts
*   **AWS Account**: With permission to create IAM Roles, Lambda Functions, ECR Repos, and EventBridge Schedules.
*   **External API Keys**: OpenAI and Resend API keys ready to be put into Secrets Manager.
