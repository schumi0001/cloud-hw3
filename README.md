# Assignment 3 - Photo Album

Furkan Gokmenoglu (fg726)
Individual submission.

This application searches uploaded photos using labels.
It uses S3, Lambda, Rekognition, Lex, Elasticsearch and API Gateway.

Website:
http://hw3-frontend-fg726.s3-website-us-east-1.amazonaws.com

## Folders

- front-end/: website, generated SDK and frontend buildspec
- lambda-functions/: index-photos and search-photos
- other-scripts/: backend scripts, tests, AWS configuration and CloudFormation

## CodePipeline

Both pipelines use this repository's main branch.

- hw3-backend-pipeline: builds and deploys both Lambda functions.
- hw3-frontend-pipeline: uploads the frontend to S3.

A push to main starts both pipelines.
The buildspecs are other-scripts/backend-buildspec.yml
and front-end/buildspec.yml.

## Checks

    python3 -m unittest discover -s other-scripts/tests -v
    python3 other-scripts/package.py
    node front-end/tests/sdk.cjs

Search dependencies are listed in other-scripts/requirements.txt.
The running search function uses a Lambda dependency layer.

## AWS configuration

other-scripts/swagger.json contains the deployed API definition.
other-scripts/lex-config.json contains the current search intent and slot settings.
SearchIntent uses an optional RawQuery slot to capture labels outside its examples.
The search function keeps multiword labels together.
Connection passwords and generated frontend config.js files are excluded.

## CloudFormation

The template is other-scripts/cloudformation/template.json.
Deployment commands are in that folder's README.
