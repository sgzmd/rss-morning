#!/bin/bash

shopt -s expand_aliases
# 1. Alias for brevity (points AWS CLI to LocalStack)
# If you don't have aws cli installed, you can run this inside a container, 
# but assuming you have it on your linux server:
alias awslocal="aws --endpoint-url=http://docker.home:4566 --region us-east-1"

echo ">>> Creating SSM Parameters..."
awslocal ssm put-parameter --name "/rss-morning/OPENAI_API_KEY" --value "sk-fake-key" --type String --overwrite
awslocal ssm put-parameter --name "/rss-morning/GOOGLE_API_KEY" --value "AIza-fake-key" --type String --overwrite
awslocal ssm put-parameter --name "/rss-morning/RESEND_API_KEY" --value "re_fake_key" --type String --overwrite

echo ">>> Creating Lambda Function..."

awslocal lambda delete-function \
    --function-name rss-morning-pipelin

# We reference the Docker image we built earlier
awslocal lambda create-function \
    --function-name rss-morning-pipeline \
    --package-type Image \
    --code ImageUri=rss-morning:local \
    --role arn:aws:iam::000000000000:role/irrelevant-in-localstack \
    --environment Variables="{RSS_MORNING_USE_SSM=true,RSS_MORNING_LOG_STDOUT=1}" \
    --timeout 60

echo ">>> Done. Ready to invoke."