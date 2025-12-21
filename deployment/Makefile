# Makefile for AWS Deployment Guide

# Requires: pandoc
# Install: brew install pandoc

GUIDE_NAME = aws-deployment-guide
SRC = 00_overview.md \
      01_security_principles.md \
      02_prerequisites.md \
      03_app_preparation.md \
      04_networking_iam.md \
      05_infrastructure.md \
      06_pipeline.md \
      07_scheduling.md

all: html

html:
	pandoc $(SRC) -o $(GUIDE_NAME).html --toc --metadata title="RSS-Morning AWS Deployment Guide" --standalone --css style.css --embed-resources -V mainfont="system-ui" --include-in-header mermaid-init.html

clean:
	rm -f $(GUIDE_NAME).html
