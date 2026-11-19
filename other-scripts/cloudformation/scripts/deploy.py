#!/usr/bin/env python3
"""Create or update this assignment's separate stack; do not modify live resources."""
import argparse
import json
import re
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
OWNER_TAG = {"Key": "ManagedBy", "Value": "hw3-cloudformation"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="hw3-cfn-fg726")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,22}", args.stack_name):
        raise SystemExit("Use a lowercase stack name of 1-23 letters, numbers or hyphens.")
    session = boto3.Session(region_name=args.region)
    cf = session.client("cloudformation")
    body = (ROOT / "template.json").read_text()
    validation = cf.validate_template(TemplateBody=body)
    print("AWS template validation passed.")
    try:
        stack = cf.describe_stacks(StackName=args.stack_name)["Stacks"][0]
    except ClientError as error:
        if error.response["Error"]["Code"] != "ValidationError" or "does not exist" not in error.response["Error"]["Message"]:
            raise
        stack = None

    if stack:
        if OWNER_TAG not in stack.get("Tags", []):
            raise SystemExit("The existing stack has a different owner tag. Choose another stack name.")
        if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"):
            raise SystemExit("Stack is not ready for an update: " + stack["StackStatus"])
        parameters = [{"ParameterKey": item["ParameterKey"], "UsePreviousValue": True}
                      for item in validation.get("Parameters", [])]
        try:
            cf.update_stack(
                StackName=args.stack_name, TemplateBody=body,
                Parameters=parameters, Capabilities=["CAPABILITY_IAM"],
                Tags=[OWNER_TAG, {"Key": "Project", "Value": "cloud-hw3"}]
            )
        except ClientError as error:
            if error.response["Error"]["Message"] == "No updates are to be performed.":
                print("Stack already matches this template.")
                return
            raise
        print("Started stack update:", args.stack_name)
        print("Wait for UPDATE_COMPLETE before running scripts/publish_frontend.py.")
        return

    lambdas = session.client("lambda")
    index = lambdas.get_function_configuration(FunctionName="index-photos")
    search = lambdas.get_function_configuration(FunctionName="search-photos")
    index_env = index.get("Environment", {}).get("Variables", {})
    search_env = search.get("Environment", {}).get("Variables", {})
    for env in (index_env, search_env):
        if not all(env.get(key) for key in ("ES_ENDPOINT", "ES_USERNAME", "ES_PASSWORD")):
            raise SystemExit("The existing Lambda is missing Elasticsearch connection settings.")
    if any(index_env[key] != search_env[key]
           for key in ("ES_ENDPOINT", "ES_USERNAME", "ES_PASSWORD")):
        raise SystemExit("The two live functions use different Elasticsearch settings; check before deploying.")
    if search["Runtime"] != "python3.12":
        raise SystemExit("The current search runtime differs from Python 3.12. Check its layer compatibility first.")
    layers = [item["Arn"] for item in search.get("Layers", [])]
    if not layers:
        raise SystemExit("No dependency layer found on search-photos; its imports must be packaged before deploying.")
    settings = {
        "ElasticsearchEndpoint": search_env["ES_ENDPOINT"],
        "ElasticsearchUsername": search_env["ES_USERNAME"],
        "ElasticsearchPassword": search_env["ES_PASSWORD"],
        "LexBotId": search_env["LEX_BOT_ID"],
        "LexBotAliasId": search_env["LEX_BOT_ALIAS_ID"],
        "LexLocaleId": search_env["LEX_LOCALE_ID"],
        "SearchLayerArns": ",".join(layers),
        "IndexArchitecture": index.get("Architectures", ["x86_64"])[0],
        "SearchArchitecture": search.get("Architectures", ["x86_64"])[0],
    }
    cf.create_stack(
        StackName=args.stack_name, TemplateBody=body,
        Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in settings.items()],
        Capabilities=["CAPABILITY_IAM"],
        Tags=[OWNER_TAG, {"Key": "Project", "Value": "cloud-hw3"}],
        TimeoutInMinutes=15
    )
    print("Started stack creation:", args.stack_name)
    print("Existing service settings supplied without printing or saving credentials.")
    print("Check CloudFormation for CREATE_COMPLETE, then run scripts/publish_frontend.py.")

if __name__ == "__main__":
    main()
