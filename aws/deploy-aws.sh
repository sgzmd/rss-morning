#!/bin/bash
set -e

# Configuration
APP_NAME="rss-morning"
FUNCTION_NAME="rss-morning-pipeline"
REGION=$(aws configure get region)
REGION=${REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${APP_NAME}:latest"

# Helper to check if a command exists
command_exists() {
  command -v "$1" >/dev/null 2>&1
}

if ! command_exists aws; then
  echo "Error: aws cli is not installed."
  exit 1
fi

if ! command_exists docker; then
  echo "Error: docker is not installed."
  exit 1
fi

echo ">>> Deploying to AWS Account: $ACCOUNT_ID in Region: $REGION"

# 1. Login to ECR
echo ">>> Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# 2. Create ECR Repository if it doesn't exist
echo ">>> Checking ECR Repository..."
if ! aws ecr describe-repositories --repository-names "$APP_NAME" >/dev/null 2>&1; then
  echo ">>> Creating ECR Repository '$APP_NAME'..."
  aws ecr create-repository --repository-name "$APP_NAME"
else
  echo ">>> ECR Repository '$APP_NAME' already exists."
fi

# 3. Build Docker Image
echo ">>> Building Docker image..."
# Using Dockerfile.aws as implied by context
docker build -t "${APP_NAME}:latest" -f Dockerfile.aws .

# 4. Tag and Push
echo ">>> Tagging image as $ECR_URI..."
docker tag "${APP_NAME}:latest" "$ECR_URI"

echo ">>> Pushing image to ECR..."
docker push "$ECR_URI"

# 5. SSM Parameters (Optional - commented out for security)
echo ">>> SSM Parameters: skipping automated creation for real AWS."
# echo "    Please set parameters using the AWS Console or CLI:"
# echo "    /rss-morning/OPENAI_API_KEY"
# echo "    /rss-morning/GOOGLE_API_KEY"
# echo "    /rss-morning/RESEND_API_KEY"

# 6. Deploy Lambda Function
echo ">>> Deploying Lambda Function..."

if aws lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
  echo ">>> Function '$FUNCTION_NAME' exists. Updating code..."
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --image-uri "$ECR_URI" \
    --publish
  
  # Optionally update configuration if needed
  # aws lambda update-function-configuration ...
else
  echo ">>> Function '$FUNCTION_NAME' does not exist."
  echo ">>> Attempting to create function..."
  
  # Try to find a role with 'lambda' in the name if ROLE_ARN is not set
  if [ -z "$ROLE_ARN" ]; then
    echo ">>> Searching for an existing Lambda role..."
    ROLE_ARN=$(aws iam list-roles --query "Roles[?contains(RoleName, 'lambda')].Arn" --output text | head -n 1)
  fi

  if [ -z "$ROLE_ARN" ] || [ "$ROLE_ARN" == "None" ]; then
    echo "!!! Error: Cannot create function without an IAM Role."
    echo "!!! Please export ROLE_ARN='arn:aws:iam::...:role/...' and re-run."
    exit 1
  fi
  
  echo ">>> Creating function using role: $ROLE_ARN"
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --package-type Image \
    --code ImageUri="$ECR_URI" \
    --role "$ROLE_ARN" \
    --environment Variables="{RSS_MORNING_USE_SSM=true,RSS_MORNING_LOG_STDOUT=1}" \
    --timeout 60
fi

echo ">>> Deployment Complete!"
