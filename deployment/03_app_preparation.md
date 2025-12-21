# Part 3: Application Preparation

Before deploying, we must "harden" the application to ensure it is secure and observability-friendly.

## 1. Security: Create `.dockerignore`
**Why?** By default, Docker copies *everything*. We do not want to bake `.git` history, local secrets, or virtual environments into the image. This bloats the image and leaks sensitive data.

**Action**: Create `.dockerignore` in the project root.
```text
.git
.gitignore
.dockerignore
venv/
__pycache__/
*.pyc
logs/
configs/*.xml
!configs/*.xml.example
.env
# Exclude pre-computed embeddings if you want a fresh start
query_embeddings.json
```

> [!NOTE]
> **Strategy Decision**: The above `.dockerignore` excludes `configs/*.xml` (except examples). This assumes you will either:
> 1.  **Fetch Configs from S3** at runtime (requires S3 permissions, see Part 4).
> 2.  **Mount Configs** via EFS or Volume (complex for Fargate).
> 3.  **Bake Configs In**: If you prefer to bake non-sensitive configs into the image, remove `configs/*.xml` from this list.

## 2. Security: Run as Non-Root
**Why?** If an attacker compromises your container running as root, they have root access to the container filesytem. Using a standardized non-privileged user greatly limits the blast radius.

**Action**: Update `Dockerfile` to create and switch to a user.
```dockerfile
# ... inside Dockerfile ...
RUN useradd -m -u 1000 appuser
USER appuser
CMD ["python", "main.py"]
```

## 3. Observability: Logging
**Why?** In the cloud, you don't SSH into a server to read a log file. You stream logs to `stdout` (standard output), which the container runtime captures and sends to CloudWatch.

**Action**: Ensure `main.py` respects `RSS_MORNING_LOG_STDOUT=1`.
```python
# main.py snippet
if os.environ.get("RSS_MORNING_LOG_STDOUT") == "1":
    # Configure logging to stream to sys.stdout
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
```
