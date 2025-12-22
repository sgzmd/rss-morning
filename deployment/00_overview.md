# Part 0: Architecture Overview

## Goal
Deploy `rss-morning` as a secure, automated, serverless batch job on AWS using **AWS Lambda**.

## The "Serverless (Lambda)" Pattern
We are using **AWS Lambda** with **Container Image Support**.

*   **Why?**
    *   **Cost**: extremely low for sporadic jobs (pay per millisecond).
    *   **Simplicity**: No cluster management.
    *   **Scalability**: Scales to zero automatically.
*   **Container Support**: Allows us to package our complex dependencies (Pandas, Numpy, NLTK) in a familiar Docker environment, bypassing the standard 250MB size limit of Lambda zip archives (supports up to 10GB images).

## Architecture Diagram

```mermaid
flowchart LR
    Scheduler[EventBridge Scheduler]
    
    subgraph Region
        Lambda[AWS Lambda Function\n(Container Image)]
    end
    
    ECR[Amazon ECR]
    SSM[Systems Manager / Secrets Manager]
    Internet((Internet))
    CW[CloudWatch Logs]

    Scheduler -->|Triggers cron| Lambda
    Lambda -->|Pulls Image| ECR
    Lambda -->|Reads Secrets| SSM
    Lambda -->|Fetches Feeds| Internet
    Lambda -->|Logs| CW
```

## Key Components
1.  **Docker Container**: The unit of deployment.
2.  **Amazon ECR**: Hosting the Docker container image.
3.  **AWS Lambda**: The compute service running the container.
4.  **EventBridge Scheduler**: Triggers the function every morning.
5.  **IAM**: Least-privilege roles for the function.

## Trade-off Analysis: Lambda vs. Fargate

| Feature | AWS Lambda (Container) | AWS Fargate (ECS) |
| :--- | :--- | :--- |
| **Cost** | **Cheapest** for short, sporadic jobs (< 15 mins). Pay per ms. | Higher minimum cost. Pay for provisioned CPU/RAM per second (min 1 min). |
| **Duration Limit** | **Strict 15-minute timeout**. | Unlimited duration. |
| **Startup (Cold Start)** | Can be slow (seconds) for large container images, but negligible for batch jobs. | Slower startup (minutes) to provision task. |
| **Compute Power** | Up to 10GB RAM / 6 vCPU. | Customizable vCPU/RAM combos (up to 16 vCPU / 120GB). |
| **Networking** | Can run in VPC (slower cold start, needs NAT Gateway for internet) or Public (fast, cheap). | Running in Public Subnet allows direct internet access without NAT Gateway. |

**Recommendation**: Since the `rss-morning` job typically completes in under 15 minutes, **AWS Lambda** is the most cost-effective and simple solution. If the job grows to exceed 15 minutes, we can easily migrate the same Docker image to Fargate.
