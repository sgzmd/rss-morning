# Part 1: Security First Principles

Before running a single command, we must establish the security rules. In AWS, security is "Job Zero". The default settings in many tutorials are **insecure** (e.g., using Admin keys, public S3 buckets).

## 1. Least Privilege
**Concept**: A component (like our script) should have *only* the permissions it needs to do its job, and nothing more.

*   **Application Needs**: Read RSS feeds (Internet), Read API Keys (SSM), Write Logs (CloudWatch), **Read S3 (Optional for Configs)**.
*   **Application DOES NOT Need**: EC2 admin rights, User management, Open Inbound Ports.

## 2. Secrets Management
**Never** hardcode API keys in code or Dockerfiles.

*   **Bad**: `ENV OPENAI_API_KEY=sk-...` in Dockerfile (visible in image history).
*   **Good**: Inject at runtime from a secure vault. We will use **AWS Systems Manager Parameter Store** (SecureString).

## 3. Network Isolation
**Concept**: Control traffic flow.

*   **Inbound**: Our batch job needs **zero** inbound ports open. No SSH, no HTTP listener. It initiates connections; it does not receive them.
*   **Outbound**: Needs HTTPS (443) only.

## 4. Immutable Infrastructure
Once a Docker image is built and tested, it should not change. We deploy specific versions (tags), not just "latest".

## 5. Observability as Security
If you can't see it, you can't secure it. We will enforce:

*   **Structured Logging**: Sending logs to CloudWatch.
*   **Cost Monitoring**: Serverless limits cost exposure (code stops running = billing stops).
