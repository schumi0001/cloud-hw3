# Photo album update: 1
import base64
import hashlib
import json
import os
from urllib.error import HTTPError
from urllib.parse import unquote_plus
from urllib.request import Request, urlopen

import boto3

s3 = boto3.client("s3")
rekognition = boto3.client("rekognition")

ES_ENDPOINT = os.environ["ES_ENDPOINT"].rstrip("/")
credentials = (
    f'{os.environ["ES_USERNAME"]}:{os.environ["ES_PASSWORD"]}'
)
AUTH = base64.b64encode(credentials.encode()).decode()


def lambda_handler(event, context):
    indexed = 0

    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if key.endswith("/"):
            continue

        # Read custom labels from the uploaded object's metadata.
        metadata = s3.head_object(Bucket=bucket, Key=key)
        custom_labels = metadata.get("Metadata", {}).get(
            "customlabels", ""
        ).split(",")

        # Keep every label returned by Rekognition.
        detection = rekognition.detect_labels(
            Image={"S3Object": {"Bucket": bucket, "Name": key}},
            MaxLabels=1000
        )
        detected_labels = [
            label["Name"] for label in detection["Labels"]
        ]

        # Normalize labels and remove duplicates.
        labels = list(dict.fromkeys(
            label.strip().lower()
            for label in custom_labels + detected_labels
            if label.strip()
        ))

        document = {
            "objectKey": key,
            "bucket": bucket,
            "createdTimestamp": metadata["LastModified"].strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "labels": labels
        }

        print("PHOTO_DOCUMENT:", json.dumps(document))

        # Re-uploading the same object updates its existing record.
        document_id = hashlib.sha256(
            f"{bucket}/{key}".encode()
        ).hexdigest()

        request = Request(
            f"{ES_ENDPOINT}/photos/_doc/{document_id}"
            "?refresh=wait_for",
            data=json.dumps(document).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {AUTH}"
            },
            method="PUT"
        )

        try:
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        except HTTPError as error:
            print(
                "ELASTICSEARCH_ERROR:",
                error.code,
                error.read().decode("utf-8", errors="replace")
            )
            raise

        print("INDEX_RESULT:", json.dumps(result))
        indexed += 1

    return {"indexed": indexed}