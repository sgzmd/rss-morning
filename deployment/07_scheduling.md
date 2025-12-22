# Part 7: Scheduling

## Overview
We use **Amazon EventBridge** to trigger the Lambda function on a defined schedule.

## Cron Syntax
The schedule uses standard cron syntax:
`cron(Minutes Hours Day-of-month Month Day-of-week Year)`
*   Note: AWS Cron fields are slightly different from standard Linux cron.

**Example**: Run at 07:00 AM UTC every day.
`cron(0 7 * * ? *)`

## CDK Implementation
As shown in `05_infrastructure.md`, the `aws-events` module makes this simple:

```python
rule = events.Rule(
    self, "DailyRunRule",
    schedule=events.Schedule.cron(minute="0", hour="7")
)
rule.add_target(targets.LambdaFunction(docker_func))
```

## Manual Setup (Console)
1.  Go to **Amazon EventBridge** > **Rules**.
2.  Click **Create rule**.
3.  **Name**: `rss-morning-daily`.
4.  **Schedule type**: **Schedule** -> **A fine-grained schedule (Cron)**.
5.  **Cron expression**: `0 7 * * ? *`.
6.  **Target 1**: **AWS Lambda function**.
7.  Select your `rss-morning` function.
8.  Create.
