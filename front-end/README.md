# Photo Album - Frontend

This is the frontend for Assignment 3. It uses HTML, CSS and JavaScript.
Users can search photos and upload JPG or PNG files with optional custom labels.

Website: http://hw3-frontend-fg726.s3-website-us-east-1.amazonaws.com

## Files

- public/index.html: search and upload forms
- public/app.js: calls the API and displays photos
- public/styles.css: basic page styles
- public/sdk/: the API Gateway-generated JavaScript SDK

The SDK has two small fixes to keep binary image data when uploading files.

## Deployment

Push changes to main in the cloud-hw3 repository.
The hw3-frontend-pipeline pipeline runs the checks and
uploads the frontend to S3 through CodeBuild.

For manual deployment from CloudShell:

    bash front-end/scripts/deploy.sh

The script gets the API key from AWS and writes public/config.js.
That generated file is excluded from Git.

## Checks

    node front-end/tests/sdk.cjs

This checks SDK requests, image bytes and JavaScript syntax.
The optional browser checks use Playwright:

    node front-end/tests/browser.cjs
