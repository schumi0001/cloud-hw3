import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

deploy = load("deploy")
publish = load("publish_frontend")

class StackTests(unittest.TestCase):
    def setUp(self):
        self.template = json.loads((ROOT / "template.json").read_text())
        self.cf = MagicMock()
        self.lambdas = MagicMock()
        self.session = MagicMock()
        self.session.client.side_effect = lambda name: {
            "cloudformation": self.cf, "lambda": self.lambdas
        }[name]
        self.cf.validate_template.return_value = {
            "Parameters": [{"ParameterKey": name} for name in self.template["Parameters"]]
        }

    def invoke(self):
        with patch.object(deploy.boto3, "Session", return_value=self.session), \
             patch("sys.argv", ["deploy.py"]), contextlib.redirect_stdout(io.StringIO()) as output:
            deploy.main()
            return output.getvalue()

    def make_missing_stack(self):
        self.cf.describe_stacks.side_effect = ClientError({
            "Error": {"Code": "ValidationError", "Message": "Stack does not exist"}
        }, "DescribeStacks")
        env = {
            "ES_ENDPOINT": "https://example.invalid",
            "ES_USERNAME": "test-user",
            "ES_PASSWORD": "TEST-ONLY-PRIVATE-PASSWORD",
            "LEX_BOT_ID": "bot123",
            "LEX_BOT_ALIAS_ID": "alias123",
            "LEX_LOCALE_ID": "en_US",
        }
        self.config = {
            "Environment": {"Variables": env}, "Runtime": "python3.12",
            "Layers": [{"Arn": "arn:aws:lambda:us-east-1:123456789012:layer:test:1"}],
            "Architectures": ["x86_64"]
        }
        self.lambdas.get_function_configuration.return_value = self.config

    def test_inline_sources_match_editable_files(self):
        for logical, folder in (("IndexFunction", "index-photos"), ("SearchFunction", "search-photos")):
            self.assertEqual(
                self.template["Resources"][logical]["Properties"]["Code"]["ZipFile"],
                (ROOT / "lambda-functions" / folder / "index.py").read_text()
            )

    def test_source_edits_change_both_inline_code_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ROOT / "lambda-functions", target / "lambda-functions")
            (target / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts/update_template.py", target / "scripts/update_template.py")
            (target / "template.json").write_text(json.dumps(self.template))
            for folder in ("index-photos", "search-photos"):
                source = target / "lambda-functions" / folder / "index.py"
                source.write_text(source.read_text() + "\n# Code update demonstration\n")
            subprocess.run([sys.executable, str(target / "scripts/update_template.py")],
                           check=True, capture_output=True)
            updated = json.loads((target / "template.json").read_text())
            for logical in ("IndexFunction", "SearchFunction"):
                self.assertIn("# Code update demonstration", updated["Resources"][logical]["Properties"]["Code"]["ZipFile"])
                self.assertNotEqual(updated["Resources"][logical]["Properties"]["Code"],
                                    self.template["Resources"][logical]["Properties"]["Code"])

    def test_foreign_stack_is_not_modified(self):
        self.cf.describe_stacks.return_value = {"Stacks": [{
            "StackStatus": "CREATE_COMPLETE", "Tags": []
        }]}
        with self.assertRaises(SystemExit):
            self.invoke()
        self.cf.update_stack.assert_not_called()
        self.cf.create_stack.assert_not_called()

    def test_update_preserves_every_existing_parameter(self):
        self.cf.describe_stacks.return_value = {"Stacks": [{
            "StackStatus": "CREATE_COMPLETE", "Tags": [deploy.OWNER_TAG]
        }]}
        self.invoke()
        request = self.cf.update_stack.call_args.kwargs
        self.assertEqual(
            request["Parameters"],
            [{"ParameterKey": name, "UsePreviousValue": True}
             for name in self.template["Parameters"]]
        )
        self.lambdas.get_function_configuration.assert_not_called()

    def test_create_keeps_password_out_of_template_and_output(self):
        self.make_missing_stack()
        output = self.invoke()
        request = self.cf.create_stack.call_args.kwargs
        password = self.config["Environment"]["Variables"]["ES_PASSWORD"]
        self.assertNotIn(password, request["TemplateBody"])
        self.assertNotIn(password, output)
        params = {p["ParameterKey"]: p["ParameterValue"] for p in request["Parameters"]}
        self.assertEqual(params["ElasticsearchPassword"], password)
        self.assertTrue(json.loads(request["TemplateBody"])["Parameters"]["ElasticsearchPassword"]["NoEcho"])

    def test_missing_layer_prevents_stack_creation(self):
        self.make_missing_stack()
        self.config["Layers"] = []
        with self.assertRaises(SystemExit):
            self.invoke()
        self.cf.create_stack.assert_not_called()

    def test_sdk_is_reconfigured_for_new_api_and_key(self):
        url = "https://newapi.execute-api.us-east-1.amazonaws.com/v1"
        files = publish.prepare_files(url, "TEST-ONLY-KEY")
        sdk = files["sdk/apigClient.js"][0].decode()
        self.assertIn(url, sdk)
        self.assertNotIn("ilaz71kqzd", sdk)
        self.assertIn("TEST-ONLY-KEY", files["config.js"][0].decode())
        self.assertNotIn("config.example.js", files)
        self.assertIn("<h1>Photo Album</h1>", files["index.html"][0].decode())

if __name__ == "__main__":
    unittest.main()
