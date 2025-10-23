# AWS Deployment Guide for RSS Morning

This document outlines the recommended approach for deploying the RSS Morning application to AWS. The goal is to create a "production-ready" setup that is automated, secure, and requires minimal manual intervention.

## Recommended Architecture

We will use a serverless architecture, which is cost-effective and requires no server management. The core components are:

*   **AWS Lambda**: To run the application code without provisioning or managing servers.
*   **Amazon ECR (Elastic Container Registry)**: To store the application's container image.
*   **Amazon EventBridge**: To trigger the Lambda function on a schedule (e.g., every morning at 05:00 GMT).
*   **AWS Systems Manager Parameter Store**: To securely store and manage secrets like API keys.
*   **Amazon CloudWatch**: For logging and monitoring.

This setup ensures that the application runs reliably on a schedule, and secrets are never hardcoded in the source code.

## Deployment Steps

### 1. Prerequisites

Before you begin, you will need:

*   An AWS account.
*   The [AWS CLI](https://aws.amazon.com/cli/) installed and configured.
*   [Docker](https://www.docker.com/get-started) installed and running on your local machine.

### 2. Packaging the Application

We will package the application as a Docker container. This approach bundles the code and all its dependencies into a single, portable image.

#### a. Create `requirements.txt`

Create a file named `requirements.txt` in the root of your project with the following content:

```
requests
lxml
readability-lxml
feedparser
resend
google-generativeai
Jinja2
MarkupSafe
```

#### b. Create a `Dockerfile`

Create a file named `Dockerfile` in the root of your project with the following content:

```dockerfile
# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Set the entry point for the container
CMD ["python3", "main.py"]
```

This `Dockerfile` creates a container image with Python 3.11, installs the dependencies from `requirements.txt`, and copies the application code into the container.

### 3. Managing Configuration and Secrets

Your application relies on several secrets (API keys). These should be stored securely in **AWS Systems Manager Parameter Store**.

1.  **Open the AWS Management Console** and navigate to **Systems Manager > Parameter Store**.
2.  Create the following parameters with the type `SecureString`:

    *   `/rss-morning/GEMINI_API_KEY`
    *   `/rss-morning/SENDGRID_API_KEY`
    *   `/rss-morning/RESEND_API_KEY`

    For each parameter, paste the corresponding API key from your `env.fish` file as the value.

### 4. Setting up the AWS Infrastructure

#### a. Create an ECR Repository

This is where you will store your Docker image.

```bash
aws ecr create-repository --repository-name rss-morning --image-scanning-configuration scanOnPush=true
```

#### b. Build and Push the Docker Image

1.  **Log in to ECR**: Replace `123456789012` with your AWS account ID and `us-east-1` with your preferred region.

    ```bash
    aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 123456789012.dkr.ecr.us-east-1.amazonaws.com
    ```

2.  **Build the Docker image**:

    ```bash
    docker build -t rss-morning .
    ```

3.  **Tag the image**:

    ```bash
    docker tag rss-morning:latest 123456789012.dkr.ecr.us-east-1.amazonaws.com/rss-morning:latest
    ```

4.  **Push the image to ECR**:

    ```bash
    docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/rss-morning:latest
    ```

#### c. Create an IAM Role for the Lambda Function

The Lambda function needs permission to read from Parameter Store and write logs to CloudWatch.

1.  **Create a file named `lambda-trust-policy.json`**:

    ```json
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Principal": {
            "Service": "lambda.amazonaws.com"
          },
          "Action": "sts:AssumeRole"
        }
      ]
    }
    ```

2.  **Create the IAM role**:

    ```bash
    aws iam create-role --role-name RssMorningLambdaRole --assume-role-policy-document file://lambda-trust-policy.json
    ```

3.  **Create a permissions policy**: Create a file named `lambda-permissions-policy.json`. Replace `123456789012` with your AWS account ID.

    ```json
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": "arn:aws:logs:*:*:*"
            },
            {
                "Effect": "Allow",
                "Action": "ssm:GetParameters",
                "Resource": "arn:aws:ssm:us-east-1:123456789012:parameter/rss-morning/*"
            }
        ]
    }
    ```

4.  **Attach the policy to the role**:

    ```bash
    aws iam put-role-policy --role-name RssMorningLambdaRole --policy-name RssMorningLambdaPolicy --policy-document file://lambda-permissions-policy.json
    ```

#### d. Create the Lambda Function

We will create a Lambda function that uses the container image from ECR.

1.  **Create the Lambda function**: Replace the `image-uri` and `role` ARN with your own values.

    ```bash
    aws lambda create-function \
        --function-name rss-morning \
        --package-type Image \
        --code ImageUri=123456789012.dkr.ecr.us-east-1.amazonaws.com/rss-morning:latest \
        --role arn:aws:iam::123456789012:role/RssMorningLambdaRole \
        --timeout 300 \
        --memory-size 512
    ```

2.  **Update the Lambda function's code to fetch secrets**: You will need to modify your Python code to fetch the secrets from Parameter Store instead of environment variables. Here is an example of how you could do this in your `runner.py` or a new config module:

    ```python
    import boto3

    def get_secrets():
        ssm = boto3.client('ssm')
        parameters = ssm.get_parameters(
            Names=[
                '/rss-morning/GEMINI_API_KEY',
                '/rss-morning/SENDGRID_API_KEY',
                '/rss-morning/RESEND_API_KEY'
            ],
            WithDecryption=True
        )
        secrets = {p['Name']: p['Value'] for p in parameters['Parameters']}
        return secrets

    # In your main logic, you would then get the secrets and set them as environment variables
    # before the rest of your application runs.
    # For example:
    # secrets = get_secrets()
    # os.environ['GEMINI_API_KEY'] = secrets.get('/rss-morning/GEMINI_API_KEY')
    # ... and so on for the other keys.
    ```
    You will also need to add `boto3` to your `requirements.txt`.

3. **Override command in Lambda**: The command line arguments you use to run the application locally need to be passed to the container in the Lambda environment. You can do this by overriding the container's `CMD` in the Lambda function configuration.

    ```bash
     aws lambda update-function-configuration \
        --function-name rss-morning \
        --ephemeral-storage '{"Size": 1024}' \
        --environment '{"Variables": {"AWS_REGION": "us-east-1"}}' \
        --cli-binary-format raw-in-base64-out \
        --payload-version 2 \
        --command '["python3", "main.py", "-n", "10", "--max-age-hours", "24", "--summary", "--email-to", "sigizmund@gmail.com", "--email-from", "mailer@r-k.co", "--log-level", "INFO"]'
    ```

#### e. Create an EventBridge Rule

This rule will trigger your Lambda function every morning.

1.  **Create the rule**: This creates a rule that runs every day at 05:00 GMT.

    ```bash
    aws events put-rule \
        --name "RssMorningDailyTrigger" \
        --schedule-expression "cron(0 5 * * ? *)"
    ```

2.  **Add the Lambda function as a target**:

    ```bash
    aws events put-targets \
        --rule "RssMorningDailyTrigger" \
        --targets "Id"="1","Arn"="arn:aws:lambda:us-east-1:123456789012:function:rss-morning"
    ```

3.  **Add permission for EventBridge to invoke the Lambda**:

    ```bash
    aws lambda add-permission \
        --function-name rss-morning \
        --statement-id "EventBridgeInvoke" \
        --action "lambda:InvokeFunction" \
        --principal "events.amazonaws.com" \
        --source-arn "arn:aws:events:us-east-1:123456789012:rule/RssMorningDailyTrigger"
    ```

## Deployment Workflow

Once the initial setup is complete, updating the application is simple:

1.  **Make your code changes.**
2.  **Build the new Docker image.**
3.  **Push the new image to ECR with a new tag (or `latest`).**
4.  **Update the Lambda function to use the new image tag.**

    ```bash
    aws lambda update-function-code \
        --function-name rss-morning \
        --image-uri 123456789012.dkr.ecr.us-east-1.amazonaws.com/rss-morning:new-version-tag
    ```

## Alternative Approaches

*   **EC2 Instance with Cron**: You could run the application on a small EC2 instance (like a `t3.micro`) and use a standard cron job to schedule it. This gives you more control over the environment but also requires you to manage the server, its security, and OS updates. The serverless approach with Lambda is generally more cost-effective and requires less maintenance for this type of workload.
*   **AWS Fargate**: Fargate is another serverless option for running containers. It's more suited for long-running services or larger applications. For a simple, scheduled task like this, Lambda is a more direct and simpler solution.

This guide provides a complete and robust solution for deploying your RSS Morning application on AWS. By following these steps, you can achieve an automated and secure deployment that runs your application reliably every morning.
