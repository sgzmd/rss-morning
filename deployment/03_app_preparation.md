# Part 3: Application Preparation

## 1. Dockerfile Optimization
To run on AWS Lambda via Container Images, your `Dockerfile` needs to be compatible with Lambda's execution environment.

### Using AWS Lambda Base Images (Recommended)
The easiest way is to use AWS provided base images.
```dockerfile
FROM public.ecr.aws/lambda/python:3.11

# Copy requirements.txt
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# Install the specified packages
RUN pip install -r requirements.txt

# Copy function code
COPY . ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler (could be a specific lambda_handler function)
CMD [ "main.lambda_handler" ] 
```

### Using Custom Base Images (Your current setup)
If you stick with your standard `python:3.11-slim` image, you must install the **AWS Lambda Runtime Interface Client (RIC)**.

```dockerfile
# ... existing setup ...
RUN pip install awslambdaric

# Set entrypoint to the RIC
ENTRYPOINT [ "python", "-m", "awslambdaric" ]
CMD [ "main.lambda_handler" ]
```

**Recommendation**: Since `rss-morning` is a CLI tool, you will need a small adapter (wrapper) to make it invokable by Lambda.

## 2. Create a Lambda Adapter
Create a small file `lambda_main.py` (or add to `main.py`) to bridge the Lambda event to your CLI logic.

```python
# main.py addition specifically for Lambda
import os
from rss_morning.cli import main

def lambda_handler(event, context):
    """
    AWS Lambda entrypoint.
    Bridge the execution to the CLI main function.
    """
    print("Starting RSS Morning job via Lambda...")
    
    # Optional: Override args based on event payload if needed
    # sys.argv = ["rss-morning", "--env", "prod"]
    
    try:
        # Run the main CLI logic
        # Note: You might need to adjust 'main()' to not sys.exit() 
        # but just return configuration/status
        main() 
        return {"statusCode": 200, "body": "Success"}
    except Exception as e:
        print(f"Job failed: {e}")
        return {"statusCode": 500, "body": str(e)}
```

## 3. Configuration & Secrets
*   **Configs**: Ensure `config.prod.xml` is copied into the image at build time (e.g., `COPY configs/config.prod.xml ${LAMBDA_TASK_ROOT}/configs/config.xml`).
*   **Secrets**: Do **not** bake secrets into the image.
    *   The code should attempt to read `OPENAI_API_KEY` from environment variables, which will be populated by the Lambda configuration (which in turn can rely on Secrets Manager or secure env vars).

## 4. .dockerignore
Ensure you have a `.dockerignore` to keep the image small and secure:
```text
.git
.env
venv
tests
__pycache__
my_local_config.xml
```
