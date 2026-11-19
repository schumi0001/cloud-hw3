# CloudFormation code revision: 4
import json
import logging
import os
import re
import uuid
from urllib.parse import urlparse

import boto3
import certifi
from botocore.config import Config
from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

logger = logging.getLogger()
logger.setLevel(logging.INFO)

lex = boto3.client("lexv2-runtime")
s3 = boto3.client(
    "s3",
    config=Config(signature_version="s3v4")
)

endpoint = urlparse(os.environ["ES_ENDPOINT"])
es = Elasticsearch(
    [{
        "host": endpoint.hostname,
        "port": endpoint.port or 443,
        "use_ssl": True
    }],
    http_auth=(
        os.environ["ES_USERNAME"],
        os.environ["ES_PASSWORD"]
    ),
    verify_certs=True,
    ca_certs=certifi.where(),
    timeout=10,
    max_retries=1
)


def respond(status, payload):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(payload)
    }


def extract_keywords(lex_response):
    intent = lex_response.get("sessionState", {}).get("intent", {})
    if intent.get("name") != "SearchIntent":
        return []

    slot = (intent.get("slots") or {}).get("Keywords")
    if not slot:
        return []

    entries = (
        slot.get("values", [])
        if slot.get("shape") == "List"
        else [slot]
    )

    keywords = []
    for entry in entries:
        value = entry.get("value") or {}
        text = (
            value.get("originalValue")
            or value.get("interpretedValue")
            or ""
        ).strip().lower()
        if text and text not in keywords:
            keywords.append(text)

    return keywords


def extract_search_text(lex_response):
    intent = lex_response.get("sessionState", {}).get("intent", {})
    if intent.get("name") != "SearchIntent":
        return []
    slot = (intent.get("slots") or {}).get("RawQuery") or {}
    value = slot.get("value") or {}
    text = value.get("originalValue") or value.get("interpretedValue") or ""
    text = " ".join(text.lower().split())
    if not text:
        return []

    # A quoted label is searched as one phrase, including words such as "and".
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        return [text[1:-1].strip()] if text[1:-1].strip() else []

    text, request_prefix = re.subn(
        r"^(?:please\s+)?(?:show\s+me|find|search(?:\s+for)?|look\s+for|"
        r"i\s+(?:want|would\s+like)\s+to\s+see)(?:\s+|$)", "", text, count=1
    )
    text, photo_prefix = re.subn(
        r"^(?:photos?|pictures?|images?)\s+(?:of|with|containing|showing)(?:\s+|$)",
        "", text, count=1
    )
    if request_prefix or photo_prefix:
        text = re.sub(r"\s+in\s+them[.!?]?$", "", text)
        text = text.rstrip(".!?").strip()
    if not text:
        return []

    # Keep multiword labels intact and include each requested alternative.
    phrases = [text] + re.split(r"\s+(?:and|or)\s+|\s*&\s*|,\s*", text)
    return list(dict.fromkeys(phrase.strip() for phrase in phrases if phrase.strip()))


def recognize_search(query):
    settings = {
        "botId": os.environ["LEX_BOT_ID"],
        "botAliasId": os.environ["LEX_BOT_ALIAS_ID"],
        "localeId": os.environ["LEX_LOCALE_ID"],
    }
    response = lex.recognize_text(**settings, sessionId=uuid.uuid4().hex, text=query)
    keywords = extract_keywords(response)

    # Capture every query as well, since a multi-value slot can omit one label.
    captured = lex.recognize_text(
        **settings,
        sessionId=uuid.uuid4().hex,
        text=query,
        sessionState={
            "intent": {"name": "SearchIntent", "state": "InProgress", "slots": {}},
            "dialogAction": {"type": "ElicitSlot", "slotToElicit": "RawQuery"},
        },
    )
    # Do not broaden "alloy wheel" to "wheel" using a partial custom-slot match.
    return extract_search_text(captured) or keywords


def lambda_handler(event, context):
    parameters = event.get("queryStringParameters") or {}
    query = parameters.get("q") or event.get("q") or ""

    if not isinstance(query, str) or len(query) > 1024:
        return respond(400, {
            "code": 400,
            "message": "q must be text of at most 1024 characters."
        })

    query = query.strip()
    if not query:
        return respond(200, {"results": []})

    try:
        keywords = recognize_search(query)
        logger.info("KEYWORDS: %s", json.dumps(keywords))

        if not keywords:
            return respond(200, {"results": []})

        # Return photos matching any requested keyword.
        search_body = {
            "_source": ["bucket", "objectKey", "labels"],
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase": {"labels": keyword}}
                        for keyword in keywords
                    ],
                    "minimum_should_match": 1
                }
            }
        }

        results = []
        for hit in scan(
            es,
            index=os.environ["ES_INDEX"],
            query=search_body,
            size=500,
            scroll="1m",
            request_timeout=10
        ):
            photo = hit["_source"]
            url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": photo["bucket"],
                    "Key": photo["objectKey"]
                },
                ExpiresIn=3600
            )
            results.append({
                "url": url,
                "labels": photo.get("labels", [])
            })

        logger.info("MATCH_COUNT: %s", len(results))
        return respond(200, {"results": results})

    except Exception:
        logger.exception("Photo search failed")
        return respond(500, {
            "code": 500,
            "message": "Photo search failed. Check Lambda logs."
        })
