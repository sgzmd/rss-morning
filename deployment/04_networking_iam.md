# Part 4: Networking & Identity (IAM)

We need to create the identity our app will use (`Role`) and define where it runs (`VPC`).

## 1. IAM Roles
**Why?** AWS resources are "deny by default". To allow Fargate to pull an image or write to logs, we need a **Task Execution Role**. To allow the *running app* to read secrets, we need a **Task Role**.

**Action**: Create the Execution Role (Infrastructure permissions).
```bash
# 1. Create a Trust Policy (Who can assume this role? ECS Tasks)
cat <<EOF > ecs-trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "",
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# 2. Create the Role
aws iam create-role \
    --role-name RssMorningExecutionRole \
    --assume-role-policy-document file://ecs-trust-policy.json

# 3. Attach the managed policy for basic ECS execution (Pull images, write logs)
aws iam attach-role-policy \
    --role-name RssMorningExecutionRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

**Action**: Create the Task Role (Application permissions - access to Secrets).
```bash
aws iam create-role \
    --role-name RssMorningTaskRole \
    --assume-role-policy-document file://ecs-trust-policy.json

# Allow access to SSM Parameter Store (Inline Policy for Least Privilege)
cat <<EOF > ssm-policy.json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "ssm:GetParameters",
            "Resource": "arn:aws:ssm:*:*:parameter/rss-morning/*"
        }
    ]
}
EOF

aws iam put-role-policy \
    --role-name RssMorningTaskRole \
    --policy-name AccessSecrets \
    --policy-document file://ssm-policy.json

# (Optional) Allow access to S3 for Configs
# If you plan to store configs in S3 instead of baking them in, attach this policy.
cat <<EOF > s3-policy.json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [
                "arn:aws:s3:::your-config-bucket-name",
                "arn:aws:s3:::your-config-bucket-name/*"
            ]
        }
    ]
}
EOF

aws iam put-role-policy \
    --role-name RssMorningTaskRole \
    --policy-name AccessS3Configs \
    --policy-document file://s3-policy.json
```

## 2. Networking (VPC & Security Groups)
**Why?** We need internet access to fetch RSS feeds. The simplest secure setup for a Fargate task is a **Public Subnet** with a **Security Group** that allows only outbound traffic.

**Action**: Get your default VPC ID and a Subnet ID.
```bash
# Get Default VPC ID
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)

# Get a Public Subnet ID (usually all subnets in default VPC are public)
SUBNET_ID=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --query "Subnets[0].SubnetId" --output text)
```

**Action**: Create a Security Group.
```bash
GROUP_ID=$(aws ec2 create-security-group \
    --group-name rs-morning-sg \
    --description "Security group for rss-morning batch job" \
    --vpc-id $VPC_ID \
    --query "GroupId" \
    --output text)

# Add Outbound Rule (HTTPS to everywhere) - Security Groups usually allow all outbound by default, but let's be explicit if needed.
# Note: AWS CLI create-security-group default includes Allow All Outbound. We will NOT add any Inbound rules.
```
