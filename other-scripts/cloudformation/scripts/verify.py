#!/usr/bin/env python3
"""Verify provisioned resources and exact deployed inline code. Read-only."""
import argparse
import io
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

import boto3

root = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="hw3-cfn-fg726")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack_name)["Stacks"][0]
    if {"Key": "ManagedBy", "Value": "hw3-cloudformation"} not in stack.get("Tags", []):
        raise SystemExit("This is not the assignment's separate stack.")
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise SystemExit("Stack is not complete: " + stack["StackStatus"])
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    template = json.loads((root / "template.json").read_text())
    lambdas = session.client("lambda")
    for logical, output in (("IndexFunction", "IndexFunctionName"), ("SearchFunction", "SearchFunctionName")):
        response = lambdas.get_function(FunctionName=outputs[output])
        with urlopen(response["Code"]["Location"], timeout=30) as download:
            data = download.read()
        with ZipFile(io.BytesIO(data)) as archive:
            actual = archive.read("index.py").decode("utf-8")
        expected = template["Resources"][logical]["Properties"]["Code"]["ZipFile"]
        if actual != expected:
            raise RuntimeError("Deployed source does not match template: " + logical)
        print("PASS:", logical, "deployed source matches template.")
    s3 = session.client("s3")
    s3.head_bucket(Bucket=outputs["PhotoBucketName"])
    website = s3.get_bucket_website(Bucket=outputs["FrontendBucketName"])
    if website.get("IndexDocument", {}).get("Suffix") != "index.html":
        raise RuntimeError("Website index document is missing.")
    session.client("apigateway").get_rest_api(restApiId=outputs["ApiId"])
    with urlopen(outputs["WebsiteUrl"], timeout=30) as response:
        page = response.read().decode("utf-8")
        if response.status != 200 or "<h1>Photo Album</h1>" not in page:
            raise RuntimeError("Public website did not return the expected frontend.")
    print("PASS: REST API, both buckets, and public frontend website verified.")
    print("Website:", outputs["WebsiteUrl"])

if __name__ == "__main__":
    main()
