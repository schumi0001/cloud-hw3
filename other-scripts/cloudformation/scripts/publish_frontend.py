#!/usr/bin/env python3
"""Publish the bundled website to the separate CloudFormation frontend bucket."""
import argparse
import json
import mimetypes
import re
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]

def prepare_files(api_url, api_key):
    files = {}
    for path in sorted((ROOT / "frontend").rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(ROOT / "frontend").as_posix()
        if key in ("config.js", "config.example.js"):
            continue
        data = path.read_bytes()
        if key == "sdk/apigClient.js":
            text, count = re.subn(
                r"https://[A-Za-z0-9]+\.execute-api\.[A-Za-z0-9.-]+/v1",
                api_url, data.decode("utf-8")
            )
            if count != 1:
                raise RuntimeError("Expected exactly one SDK endpoint before publishing.")
            data = text.encode("utf-8")
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        files[key] = (data, content_type)
    files["config.js"] = (
        ("window.PHOTO_ALBUM_CONFIG = " + json.dumps({"apiKey": api_key}) + ";\n").encode("utf-8"),
        "application/javascript"
    )
    return files

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="hw3-cfn-fg726")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack_name)["Stacks"][0]
    if {"Key": "ManagedBy", "Value": "hw3-cloudformation"} not in stack.get("Tags", []):
        raise SystemExit("This is not the assignment's separate CloudFormation stack.")
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise SystemExit("Wait for stack creation/update to complete: " + stack["StackStatus"])
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    api_key = session.client("apigateway").get_api_key(
        apiKey=outputs["ApiKeyId"], includeValue=True
    )["value"]
    files = prepare_files(outputs["ApiUrl"], api_key)
    s3 = session.client("s3")
    for key, (data, content_type) in files.items():
        s3.put_object(
            Bucket=outputs["FrontendBucketName"], Key=key,
            Body=data, ContentType=content_type, CacheControl="no-cache"
        )
    print("Published", len(files), "frontend files to the stack's bucket.")
    print("Website:", outputs["WebsiteUrl"])
    print("API:", outputs["ApiUrl"])

if __name__ == "__main__":
    main()
