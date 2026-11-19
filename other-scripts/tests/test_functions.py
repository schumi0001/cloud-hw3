import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "ES_ENDPOINT": "https://example.invalid",
    "ES_USERNAME": "test-user",
    "ES_PASSWORD": "test-only-password",
    "LEX_BOT_ID": "test-bot",
    "LEX_BOT_ALIAS_ID": "test-alias",
    "LEX_LOCALE_ID": "en_US"
}


def load_function(name):
    clients = {key: Mock() for key in ("s3", "rekognition", "lexv2-runtime")}
    boto = types.ModuleType("boto3")
    boto.client = Mock(side_effect=lambda service, **kw: clients[service])
    certifi = types.ModuleType("certifi")
    certifi.where = Mock(return_value="test-certificates")
    botocore = types.ModuleType("botocore")
    configuration = types.ModuleType("botocore.config")
    configuration.Config = Mock()
    elasticsearch = types.ModuleType("elasticsearch")
    elasticsearch.Elasticsearch = Mock()
    helpers = types.ModuleType("elasticsearch.helpers")
    helpers.scan = Mock(return_value=iter(()))
    modules = {
        "boto3": boto,
        "certifi": certifi,
        "botocore": botocore,
        "botocore.config": configuration,
        "elasticsearch": elasticsearch,
        "elasticsearch.helpers": helpers
    }
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / "lambda-functions" / name / "lambda_function.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules), patch.dict(os.environ, ENV):
        spec.loader.exec_module(module)
    return module


def lex_result(words=None, intent="SearchIntent"):
    slot = None if words is None else {
        "shape": "List",
        "value": {"originalValue": "composite value must not be used"},
        "values": [{"shape": "Scalar", "value": {"originalValue": word}} for word in words]
    }
    return {"sessionState": {"intent": {"name": intent, "slots": {"Keywords": slot}}}}


class IndexTests(unittest.TestCase):
    def setUp(self):
        capture = contextlib.redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        self.module = load_function("index-photos")
        self.module.s3.head_object.return_value = {
            "LastModified": datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc),
            "Metadata": {"customlabels": " Sam, Sally, ,sam "}
        }
        self.module.rekognition.detect_labels.return_value = {
            "Labels": [{"Name": "Car"}, {"Name": "Person"}, {"Name": "Sam"}]
        }
        self.module.urlopen = Mock(return_value=io.BytesIO(b'{"result":"created"}'))

    def event(self, key="red+car%2B2026%2Fphoto%E2%80%AF.png"):
        return {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}}}]}

    def test_event_decoding_metadata_and_all_detected_labels(self):
        self.assertEqual(self.module.lambda_handler(self.event(), None), {"indexed": 1})
        key = "red car+2026/photo\u202f.png"
        self.module.s3.head_object.assert_called_once_with(Bucket="test-bucket", Key=key)
        request = self.module.urlopen.call_args.args[0]
        document = json.loads(request.data)
        self.assertEqual(document["labels"], ["sam", "sally", "car", "person"])
        self.assertEqual(document["objectKey"], key)
        self.assertEqual(document["createdTimestamp"], "2026-11-17T13:00:00Z")
        self.assertEqual(document["bucket"], "test-bucket")
        identifier = hashlib.sha256(f"test-bucket/{key}".encode()).hexdigest()
        self.assertIn("/photos/_doc/" + identifier, request.full_url)
        self.assertEqual(request.method, "PUT")
        self.module.rekognition.detect_labels.assert_called_once_with(
            Image={"S3Object": {"Bucket": "test-bucket", "Name": key}}, MaxLabels=1000
        )

    def test_upload_without_custom_metadata_still_keeps_detected_labels(self):
        self.module.s3.head_object.return_value.pop("Metadata")
        self.module.lambda_handler(self.event("plain.png"), None)
        self.assertEqual(json.loads(self.module.urlopen.call_args.args[0].data)["labels"], ["car", "person", "sam"])

    def test_folder_marker_does_not_invoke_rekognition_or_index(self):
        self.assertEqual(self.module.lambda_handler(self.event("folder%2F"), None), {"indexed": 0})
        self.module.s3.head_object.assert_not_called()
        self.module.rekognition.detect_labels.assert_not_called()
        self.module.urlopen.assert_not_called()

    def test_index_failure_is_raised_for_lambda_retry(self):
        self.module.urlopen.side_effect = HTTPError("https://example.invalid", 500, "failed", {}, io.BytesIO(b'failed'))
        with self.assertRaises(HTTPError):
            self.module.lambda_handler(self.event(), None)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_function("search-photos")
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_multivalue_uses_individual_originals_not_composite_or_resolutions(self):
        response = lex_result(["Horses", "cars", "horses"])
        response["sessionState"]["intent"]["slots"]["Keywords"]["values"][0]["value"]["resolvedValues"] = ["andalusian horse"]
        self.assertEqual(self.module.extract_keywords(response), ["horses", "cars"])

    def test_scalar_uses_original_or_interpreted_value(self):
        for value in ({"originalValue": " Sam "}, {"interpretedValue": " Sam "}):
            with self.subTest(value=value):
                response = {"sessionState": {"intent": {"name": "SearchIntent", "slots": {
                    "Keywords": {"shape": "Scalar", "value": value}
                }}}}
                self.assertEqual(self.module.extract_keywords(response), ["sam"])

    def test_empty_query_returns_empty_without_lex_or_search(self):
        for event in ({}, {"queryStringParameters": {"q": "   "}}):
            result = self.module.lambda_handler(event, None)
            self.assertEqual(result["statusCode"], 200)
            self.assertEqual(json.loads(result["body"]), {"results": []})
        self.module.lex.recognize_text.assert_not_called()
        self.module.scan.assert_not_called()

    def test_invalid_query_is_rejected_before_lex(self):
        for query in (7, "x" * 1025):
            result = self.module.lambda_handler({"q": query}, None)
            self.assertEqual(result["statusCode"], 400)
        self.module.lex.recognize_text.assert_not_called()

    def test_no_lex_keywords_means_no_elasticsearch_search(self):
        for response in (lex_result(), lex_result(["unknown"], intent="FallbackIntent")):
            self.module.lex.recognize_text.return_value = response
            result = self.module.lambda_handler({"q": "unknown"}, None)
            self.assertEqual(json.loads(result["body"]), {"results": []})
        self.module.scan.assert_not_called()

    def test_two_keywords_return_all_matching_photos_with_signed_urls(self):
        self.module.lex.recognize_text.return_value = lex_result(["horses", "cars"])
        self.module.scan.return_value = iter([
            {"_source": {"bucket": "photos", "objectKey": "horse.png", "labels": ["horse", "sam"]}},
            {"_source": {"bucket": "photos", "objectKey": "car.png", "labels": ["car"]}}
        ])
        self.module.s3.generate_presigned_url.side_effect = ["https://example.invalid/horse", "https://example.invalid/car"]
        result = self.module.lambda_handler({"queryStringParameters": {"q": "show me horses and cars"}}, None)
        self.assertEqual(result["statusCode"], 200)
        photos = json.loads(result["body"])["results"]
        self.assertEqual([p["labels"] for p in photos], [["horse", "sam"], ["car"]])
        self.assertEqual([p["url"] for p in photos], ["https://example.invalid/horse", "https://example.invalid/car"])
        self.assertEqual(result["headers"]["Access-Control-Allow-Origin"], "*")
        lex_args = self.module.lex.recognize_text.call_args.kwargs
        self.assertEqual(lex_args["text"], "show me horses and cars")
        self.assertEqual(lex_args["botId"], "test-bot")
        scan_args = self.module.scan.call_args.kwargs
        self.assertEqual(scan_args["index"], "photos")
        query = scan_args["query"]["query"]["bool"]
        self.assertEqual(query["minimum_should_match"], 1)
        self.assertEqual(query["should"], [{"match_phrase": {"labels": "horses"}}, {"match_phrase": {"labels": "cars"}}])
        self.assertEqual(self.module.s3.generate_presigned_url.call_count, 2)
        self.assertEqual(self.module.s3.generate_presigned_url.call_args.kwargs["ExpiresIn"], 3600)

    def test_failed_search_returns_500_not_false_empty_success(self):
        self.module.lex.recognize_text.side_effect = RuntimeError("test service failure")
        with self.assertLogs(level="ERROR"):
            result = self.module.lambda_handler({"q": "horses"}, None)
        self.assertEqual(result["statusCode"], 500)
        self.assertEqual(json.loads(result["body"])["code"], 500)


if __name__ == "__main__":
    unittest.main()
