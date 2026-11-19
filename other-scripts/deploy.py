"""Deploy both checked packages using the CodeBuild service role and AWS CLI."""
import base64
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = "us-east-1"
FUNCTIONS = {"index-photos", "search-photos"}


def aws(*args):
    command = ["aws", "lambda", *args, "--region", REGION, "--no-cli-pager"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        # All calls below explicitly restrict AWS output; no environment values are printed.
        raise RuntimeError(result.stderr.strip() or "AWS Lambda command failed")
    return json.loads(result.stdout) if result.stdout.strip() else None


def config(name):
    return aws(
        "get-function-configuration", "--function-name", name,
        "--query", "{revision:RevisionId,hash:CodeSha256,runtime:Runtime,handler:Handler,architecture:Architectures[0]}",
        "--output", "json"
    )


def main():
    manifest = json.loads((ROOT / "dist/build-manifest.json").read_text())
    entries = manifest.get("functions", [])
    if manifest.get("region") != REGION or len(entries) != 2 or {x["functionName"] for x in entries} != FUNCTIONS:
        raise SystemExit("Build manifest must target exactly the two assignment Lambdas in us-east-1.")

    prepared = []
    # Validate every package and target before modifying either function.
    for entry in entries:
        name = entry["functionName"]
        if entry["package"] != name + ".zip":
            raise SystemExit("Unexpected Lambda package path.")
        package = ROOT / "dist" / entry["package"]
        digest = base64.b64encode(hashlib.sha256(package.read_bytes()).digest()).decode()
        if digest != entry["codeSha256"]:
            raise SystemExit(f"Package integrity check failed for {name}.")
        aws("wait", "function-updated", "--function-name", name)
        current = config(name)
        for field in ("runtime", "handler", "architecture"):
            if current[field] != entry[field]:
                raise SystemExit(f"Unexpected {field} for {name}; deployment stopped.")
        prepared.append((entry, package, current["revision"]))

    print("Deploying source commit:", manifest["sourceCommit"], flush=True)
    for entry, package, revision in prepared:
        name = entry["functionName"]
        print("Updating:", name, flush=True)
        result = aws(
            "update-function-code", "--function-name", name,
            "--zip-file", "fileb://" + str(package),
            "--revision-id", revision,
            "--query", "{name:FunctionName,hash:CodeSha256}", "--output", "json"
        )
        if result["hash"] != entry["codeSha256"]:
            raise RuntimeError(f"AWS returned an unexpected code hash for {name}.")
        aws("wait", "function-updated", "--function-name", name)
        if config(name)["hash"] != entry["codeSha256"]:
            raise RuntimeError(f"Deployed code verification failed for {name}.")
        print("Verified deployed package:", name, entry["codeSha256"], flush=True)
    print("Both Lambda functions deployed successfully.", flush=True)


if __name__ == "__main__":
    main()
