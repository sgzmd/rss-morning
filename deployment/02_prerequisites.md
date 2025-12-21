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


## 2. Configure AWS CLI
The modern way to authenticate is using the `aws login` command (requires AWS CLI v2.32.0+).

1.  Run the login command:
    ```bash
    aws login
    ```
2.  This will open your browser to authenticate with your AWS credentials (Identity Center/SSO or Console credentials).
3.  Follow the prompts in the browser and CLI.

*Note: For older CLI versions or specific CI/CD use cases, `aws configure` (static keys) or `aws configure sso` may still be used, but `aws login` is recommended for local development.*

## 3. Verify Identity
Check who you are logged in as.
```bash
aws sts get-caller-identity
```
