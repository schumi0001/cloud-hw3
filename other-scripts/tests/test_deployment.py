import base64
import contextlib
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "other-scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeployTests(unittest.TestCase):
    def setUp(self):
        capture = contextlib.redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "dist").mkdir()
        self.module = script("deploy")
        self.module.ROOT = self.root
        self.entries = []
        for name in ("index-photos", "search-photos"):
            data = ("test package for " + name).encode()
            (self.root / "dist" / f"{name}.zip").write_bytes(data)
            self.entries.append({
                "functionName": name, "package": name + ".zip",
                "codeSha256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
                "runtime": "python3.12", "handler": "lambda_function.lambda_handler", "architecture": "x86_64"
            })
        manifest = {"region": "us-east-1", "sourceCommit": "test-commit", "functions": self.entries}
        (self.root / "dist/build-manifest.json").write_text(json.dumps(manifest))

    def fake_aws(self, *args):
        if args[0] == "wait":
            return None
        name = args[args.index("--function-name") + 1]
        entry = next(e for e in self.entries if e["functionName"] == name)
        if args[0] == "get-function-configuration":
            return {"revision": name + "-revision", "hash": entry["codeSha256"],
                    "runtime": entry["runtime"], "handler": entry["handler"], "architecture": entry["architecture"]}
        if args[0] == "update-function-code":
            self.assertEqual(args[args.index("--revision-id") + 1], name + "-revision")
            self.assertTrue(args[args.index("--zip-file") + 1].endswith(name + ".zip"))
            return {"name": name, "hash": entry["codeSha256"]}
        raise AssertionError("Unexpected AWS operation")

    def test_valid_packages_update_and_verify_both_functions_with_revision_guards(self):
        self.module.aws = Mock(side_effect=self.fake_aws)
        self.module.main()
        updates = [call.args for call in self.module.aws.call_args_list if call.args[0] == "update-function-code"]
        self.assertEqual(len(updates), 2)

    def test_modified_package_is_rejected_before_aws_calls(self):
        (self.root / "dist/index-photos.zip").write_bytes(b"tampered")
        self.module.aws = Mock()
        with self.assertRaises(SystemExit):
            self.module.main()
        self.module.aws.assert_not_called()

    def test_wrong_second_target_runtime_prevents_all_updates(self):
        def respond(*args):
            result = self.fake_aws(*args)
            if args[0] == "get-function-configuration" and args[args.index("--function-name") + 1] == "search-photos":
                result["runtime"] = "python3.11"
            return result
        self.module.aws = Mock(side_effect=respond)
        with self.assertRaises(SystemExit):
            self.module.main()
        self.assertFalse(any(c.args[0] == "update-function-code" for c in self.module.aws.call_args_list))


class PackageTests(unittest.TestCase):
    def setUp(self):
        capture = contextlib.redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def test_packages_are_reproducible_and_handler_is_at_zip_root(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("index-photos", "search-photos"):
                folder = root / "lambda-functions" / name
                folder.mkdir(parents=True)
                (folder / "lambda_function.py").write_text("def lambda_handler(event, context): return {}\n")
            module = script("package")
            module.ROOT = root
            module.main()
            first = (root / "dist/index-photos.zip").read_bytes()
            module.main()
            self.assertEqual(first, (root / "dist/index-photos.zip").read_bytes())
            with ZipFile(root / "dist/index-photos.zip") as archive:
                self.assertEqual(archive.namelist(), ["lambda_function.py"])


if __name__ == "__main__":
    unittest.main()
