# CloudFormation

This template creates two Lambda functions, API Gateway, two S3 buckets
and the required permissions.

The frontend bucket has website hosting enabled.
Its URL is included in the stack outputs.

Stack name: hw3-cfn-fg726
Region: us-east-1

## Deploy

    python3 scripts/deploy.py

The helper reads the existing Lambda settings to configure access
to Lex, Elasticsearch and the search dependency layer.

After the stack reaches CREATE_COMPLETE:

    python3 scripts/publish_frontend.py
    python3 scripts/verify.py

## Update Lambda code

Edit the Python files under lambda-functions/, then run:

    python3 scripts/update_template.py
    python3 scripts/deploy.py

After UPDATE_COMPLETE, run scripts/verify.py to check the deployed code.

## Tests

    python3 -m unittest discover -s tests -v
