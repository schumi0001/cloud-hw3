"""Create deterministic Lambda packages with their expected AWS code hashes."""
import base64
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ("index-photos", "search-photos")


def main():
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    manifest = {
        "region": "us-east-1",
        "sourceCommit": os.environ.get("CODEBUILD_RESOLVED_SOURCE_VERSION", "local"),
        "functions": []
    }
    for name in FUNCTIONS:
        folder = ROOT / "lambda-functions" / name
        handler = folder / "lambda_function.py"
        if not handler.is_file():
            raise SystemExit(f"Missing handler: {handler}")
        output = destination / f"{name}.zip"
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
            files = sorted(folder.rglob("*.py"))
            for source in files:
                if "__pycache__" in source.parts:
                    continue
                data = source.read_bytes()
                compile(data, str(source), "exec")
                item = ZipInfo(source.relative_to(folder).as_posix(), (1980, 1, 1, 0, 0, 0))
                item.compress_type = ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                package.writestr(item, data)
        digest = base64.b64encode(hashlib.sha256(output.read_bytes()).digest()).decode()
        manifest["functions"].append({
            "functionName": name,
            "package": output.name,
            "codeSha256": digest,
            "runtime": "python3.12",
            "handler": "lambda_function.lambda_handler",
            "architecture": "x86_64"
        })
        print(f"Built {name}: {output.stat().st_size} bytes; SHA256 {digest}")
    (destination / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Created dist/build-manifest.json")


if __name__ == "__main__":
    main()
