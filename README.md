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

The two pipelines use these source repositories:

- https://github.com/schumi0001/cloud-hw3-backend
- https://github.com/schumi0001/cloud-hw3-frontend

Pushing to either source repository triggers its pipeline.
This repository contains the code arranged for submission.
Its buildspec paths have been adjusted for this folder layout.

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
