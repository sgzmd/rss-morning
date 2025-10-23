# Local Docker Deployment Guide for RSS Morning

This document outlines how to deploy the RSS Morning application on a Linux server that has Docker installed. The setup uses Docker to containerize the application and a standard cron job to schedule its execution.

## Prerequisites

*   A Linux server with [Docker](https://www.docker.com/get-started) installed.
*   Your project code checked out on the server.
*   The `feeds.xml` and `prompt.md` files are present in the project directory.

## Deployment Steps

### 1. Create `requirements.txt`

If you haven't already, create a file named `requirements.txt` in the root of your project with the following content:

```
requests
lxml
readability-lxml
feedparser
resend
google-generativeai
Jinja2
MarkupSafe
```

### 2. Create a `Dockerfile`

Create a file named `Dockerfile` in the root of your project. This will be used to build the application container.

```dockerfile
# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Set the entry point for the container
CMD ["python3", "main.py"]
```

### 3. Create a Docker Environment File

Your application's secrets should be stored in an environment file that is read by Docker at runtime. This file should **not** be committed to your git repository.

1.  **Create a file named `.env`** in the root of your project.

2.  **Add your secrets** to the `.env` file in `KEY=VALUE` format:

    ```
    GEMINI_API_KEY=abcd
    SENDGRID_API_KEY=abcd
    RESEND_API_KEY=abcd
    ```

3.  **Ensure this file is ignored by git** by adding `.env` to your `.gitignore` file.

### 4. Build the Docker Image

From your project's root directory, run the following command to build the Docker image:

```bash
docker build -t rss-morning:latest .
```

### 5. Schedule the Application with Cron

We will use a cron job to run the Docker container every morning at 05:00 GMT.

1.  **Open the crontab editor**:

    ```bash
    crontab -e
    ```

2.  **Add the following line** to the crontab. Make sure to replace `/path/to/your/project` with the absolute path to your project's directory.

    ```cron
    0 5 * * * cd /path/to/your/project && /usr/bin/docker run --rm --env-file .env -v $(pwd):/app rss-morning:latest python3 main.py -n 10 --max-age-hours 24 --summary --email-to sigizmund@gmail.com --email-from mailer@r-k.co --log-level INFO >> cron.log 2>&1
    ```

    **Explanation of the command:**

    *   `0 5 * * *`: This is the cron schedule for 05:00 every day.
    *   `cd /path/to/your/project`: This changes the directory to your project folder.
    *   `docker run --rm`: Runs the container and removes it after it exits.
    *   `--env-file .env`: Loads the secrets from your `.env` file.
    *   `-v $(pwd):/app`: This mounts your project directory into the container at `/app`. This ensures that the container can access `feeds.xml` and `prompt.md`, and that any output files are written to your host machine.
    *   `rss-morning:latest`: The Docker image to run.
    *   `python3 main.py ...`: The command and arguments to run inside the container.
    *   `>> cron.log 2>&1`: This redirects all output (both stdout and stderr) to a file named `cron.log` in your project directory, which is useful for debugging.

## Running Manually

To test your setup, you can run the application manually from your project directory:

```bash
docker run --rm --env-file .env -v $(pwd):/app rss-morning:latest python3 main.py -n 10 --max-age-hours 24 --summary --email-to sigizmund@gmail.com --email-from mailer@r-k.co --log-level INFO
```

## Updating the Application

To update the application:

1.  Pull the latest changes from your git repository.
2.  Rebuild the Docker image with the `latest` tag:

    ```bash
    docker build -t rss-morning:latest .
    ```

The cron job will automatically use the newly built image on its next run.
