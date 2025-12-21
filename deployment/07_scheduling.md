# Part 7: Scheduling

The final piece: making it run automatically.

## EventBridge Scheduler
**Why?** We want standard cron-like behavior. `0 7 * * ? *` means "7:00 AM every day".

**Action**:
```bash
# We need a role for the Scheduler to invoke ECS
cat <<EOF > scheduler-trust.json
{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF

aws iam create-role --role-name RssMorningSchedulerRole --assume-role-policy-document file://scheduler-trust.json

# Give permission to run ECS tasks
cat <<EOF > scheduler-policy.json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "ecs:RunTask",
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": "*"
        }
    ]
}
EOF

aws iam put-role-policy --role-name RssMorningSchedulerRole --policy-name InvokeECS --policy-document file://scheduler-policy.json
```

**Create Schedule (Cron)**:
```bash
aws scheduler create-schedule \
    --name rss-morning-daily \
    --schedule-expression "cron(0 7 * * ? *)" \
    --target '{
        "Arn": "arn:aws:ecs:REGION:ACCOUNT_ID:cluster/default",
        "RoleArn": "arn:aws:iam::ACCOUNT_ID:role/RssMorningSchedulerRole",
        "EcsParameters": {
            "TaskDefinitionArn": "arn:aws:ecs:REGION:ACCOUNT_ID:task-definition/rss-morning",
            "LaunchType": "FARGATE",
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "Subnets": ["'$SUBNET_ID'"],
                    "SecurityGroups": ["'$GROUP_ID'"],
                    "AssignPublicIp": "ENABLED"
                }
            }
        }
    }' \
    --flexible-time-window '{ "Mode": "OFF" }'
```
*Note: Replace `REGION` and `ACCOUNT_ID` appropriately.*
