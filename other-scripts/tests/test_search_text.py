import json
import uuid
import unittest
from unittest.mock import patch

from test_functions import ENV, lex_result, load_function


def captured(text=None, intent="SearchIntent", interpreted=False):
    value = {"interpretedValue" if interpreted else "originalValue": text}
    return {"sessionState": {"intent": {"name": intent, "slots": {
        "RawQuery": {"shape": "Scalar", "value": value} if text is not None else None
    }}}}


class SearchTextTests(unittest.TestCase):
    def setUp(self):
        self.module = load_function("search-photos")
        env = patch.dict("os.environ", ENV)
        env.start()
        self.addCleanup(env.stop)

    def test_labels_outside_training_examples_remain_searchable(self):
        labels = ["architecture", "building", "housing", "suburb", "aerial view",
                  "cougar", "furkanlabel1", "newlabel_" + uuid.uuid4().hex[:8]]
        prefixes = ["", "show me ", "find ", "search for ", "photos of ",
                    "pictures of ", "show me pictures of ", "I want to see "]
        for label in labels:
            for prefix in prefixes:
                with self.subTest(label=label, prefix=prefix):
                    self.assertEqual(self.module.extract_search_text(captured(prefix + label)), [label])

    def test_grouped_requests_preserve_each_multiword_label(self):
        for query in ["show me photos with sports car and aerial view in them",
                      "find photos with sports car and aerial view",
                      "Please show me photos with sports car and aerial view in them."]:
            with self.subTest(query=query):
                self.assertEqual(self.module.extract_search_text(captured(query)),
                                 ["sports car and aerial view", "sports car", "aerial view"])

    def test_alternatives_commas_and_quoted_custom_labels(self):
        self.assertEqual(self.module.extract_search_text(captured("villa, sports car or cat")),
                         ["villa, sports car or cat", "villa", "sports car", "cat"])
        self.assertEqual(self.module.extract_search_text(captured('"Sam and Sally"')),
                         ["sam and sally"])
        self.assertEqual(self.module.extract_search_text(captured("research and development")),
                         ["research and development", "research", "development"])

    def test_empty_or_wrong_intent_does_not_create_keywords(self):
        for response in [captured(), captured(""), captured("show me"), captured("photos of"),
                         captured("architecture", intent="FallbackIntent")]:
            self.assertEqual(self.module.extract_search_text(response), [])

    def test_scalar_interpreted_value_can_supply_capture(self):
        self.assertEqual(self.module.extract_search_text(captured(" Aerial  View ", interpreted=True)),
                         ["aerial view"])

    def test_partial_multivalue_result_is_supplemented(self):
        self.module.lex.recognize_text.side_effect = [
            lex_result(["cars"]), captured("show me architecture and cars")
        ]
        self.assertEqual(self.module.recognize_search("show me architecture and cars"),
                         ["architecture and cars", "architecture", "cars"])
        calls = self.module.lex.recognize_text.call_args_list
        self.assertNotEqual(calls[0].kwargs["sessionId"], calls[1].kwargs["sessionId"])
        self.assertNotIn("sessionState", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["sessionState"]["dialogAction"],
                         {"type": "ElicitSlot", "slotToElicit": "RawQuery"})
        self.assertEqual(calls[1].kwargs["sessionState"]["intent"]["slots"], {})

    def test_multiword_labels_are_not_broadened_to_partial_lex_matches(self):
        cases = [
            ("alloy wheel", ["alloy", "wheel"]),
            ("car wheel", ["car", "wheel"]),
            ("jaguar car", ["jaguar", "car"]),
        ]
        for phrase, partial in cases:
            for query in (phrase, "show me photos with " + phrase + " in them"):
                with self.subTest(query=query):
                    self.module.lex.recognize_text.side_effect = [lex_result(partial), captured(query)]
                    self.assertEqual(self.module.recognize_search(query), [phrase])

    def test_grouped_phrases_do_not_add_broader_keywords(self):
        self.module.lex.recognize_text.side_effect = [
            lex_result(["aerial view", "wheel"]), captured("show me aerial view and alloy wheel")
        ]
        keywords = self.module.recognize_search("show me aerial view and alloy wheel")
        self.assertIn("aerial view", keywords)
        self.assertIn("alloy wheel", keywords)
        self.assertNotIn("wheel", keywords)

    def test_phrase_search_uses_the_complete_label_in_elasticsearch(self):
        self.module.lex.recognize_text.side_effect = [
            lex_result(["car", "wheel"]), captured("car wheel")
        ]
        result = self.module.lambda_handler({"q": "car wheel"}, None)
        self.assertEqual(result["statusCode"], 200)
        query = self.module.scan.call_args.kwargs["query"]["query"]["bool"]
        self.assertEqual(query["should"], [{"match_phrase": {"labels": "car wheel"}}])

    def test_existing_keywords_are_used_when_freeform_capture_is_empty(self):
        self.module.lex.recognize_text.side_effect = [lex_result(["horses", "cars"]), captured()]
        self.assertEqual(self.module.recognize_search("horses and cars"), ["horses", "cars"])

    def test_fallback_can_find_a_photo_using_lex_captured_text(self):
        self.module.lex.recognize_text.side_effect = [
            lex_result(intent="FallbackIntent"), captured("show me housing")
        ]
        self.module.scan.return_value = iter([{"_source": {
            "bucket": "photos", "objectKey": "house.png", "labels": ["housing", "house"]
        }}])
        self.module.s3.generate_presigned_url.return_value = "https://example.invalid/house"
        result = self.module.lambda_handler({"q": "show me housing"}, None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(len(json.loads(result["body"])["results"]), 1)
        query = self.module.scan.call_args.kwargs["query"]["query"]["bool"]
        self.assertEqual(query["should"], [{"match_phrase": {"labels": "housing"}}])

    def test_no_captured_keywords_means_no_search(self):
        self.module.lex.recognize_text.side_effect = [lex_result(), captured()]
        result = self.module.lambda_handler({"q": "unknown"}, None)
        self.assertEqual(json.loads(result["body"]), {"results": []})
        self.module.scan.assert_not_called()

    def test_request_without_a_label_returns_empty(self):
        self.module.lex.recognize_text.side_effect = [lex_result(), captured("show me")]
        result = self.module.lambda_handler({"q": "show me"}, None)
        self.assertEqual(json.loads(result["body"]), {"results": []})
        self.module.scan.assert_not_called()

    def test_failed_capture_returns_error_instead_of_partial_results(self):
        self.module.lex.recognize_text.side_effect = [lex_result(["cars"]), RuntimeError("test failure")]
        with self.assertLogs(level="ERROR"):
            result = self.module.lambda_handler({"q": "architecture and cars"}, None)
        self.assertEqual(result["statusCode"], 500)
        self.module.scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
