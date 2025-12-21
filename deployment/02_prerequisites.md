# Part 2: Prerequisites

We assume you have `docker` installed. We need to install the tools to interact with AWS.

## 1. Install AWS CLI
The command line interface for AWS.

*   **Why?** Reproducibility. Clicking in the console is error-prone. Scripts are source control friendly.

```bash
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /
rm AWSCLIV2.pkg
```
*Verify*: `aws --version`

## 2. Install jq
A lightweight command-line JSON processor.

*   **Why?** AWS CLI outputs JSON. We need `jq` to extract IDs (like Subnet ID or Security Group ID) for variables.

```bash
brew install jq
```

## 3. Configure AWS CLI
You need your **AWS Access Key ID** and **Secret Access Key** from the AWS Console (IAM User).

```bash
aws configure
# AWS Access Key ID: <your-key-id>
# AWS Secret Access Key: <your-secret-key>
# Default region name: us-east-1 (or your preferred region)
# Default output format: json
```

## 4. Verify Identity
Check who you are logged in as.
```bash
aws sts get-caller-identity
```
