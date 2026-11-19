#!/usr/bin/env python3
"""Compare live photo searches with direct Elasticsearch results. No uploads."""
import argparse
import base64
import json
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import boto3


def photo_id(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = unquote(parsed.path.lstrip("/"))
    if ".s3" in host:
        return host.split(".s3", 1)[0], path
    if host.startswith("s3") and "/" in path:
        return tuple(path.split("/", 1))
    raise ValueError("Unexpected photo URL format")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", default="search-photos")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    client = boto3.client("lambda", region_name=args.region)
    before = client.get_function_configuration(FunctionName=args.function)
    if before.get("LastUpdateStatus") not in (None, "Successful"):
        raise SystemExit("Lambda is still updating. Run this check once the pipeline finishes.")
    env = before["Environment"]["Variables"]
    index = env.get("ES_INDEX", "photos")
    auth = base64.b64encode((env["ES_USERNAME"] + ":" + env["ES_PASSWORD"]).encode()).decode()
    headers = {"Content-Type": "application/json", "Authorization": "Basic " + auth}

    # Compare the live code hash when running from a built source repository.
    manifest = Path(__file__).resolve().parents[1] / "dist/build-manifest.json"
    if args.function == "search-photos" and manifest.is_file():
        entries = json.loads(manifest.read_text())["functions"]
        expected = next(p["codeSha256"] for p in entries if p["functionName"] == args.function)
        if before["CodeSha256"] != expected:
            raise SystemExit("Lambda does not yet match the local package. Wait for the pipeline or check its build.")

    def request(path, body, method="POST"):
        req = Request(env["ES_ENDPOINT"].rstrip("/") + path,
                      data=json.dumps(body).encode(), headers=headers, method=method)
        with urlopen(req, timeout=20) as response:
            return json.load(response)

    def documents(body):
        scroll_id = None
        results = []
        try:
            page = request("/" + index + "/_search?scroll=1m", {
                "size": 500, "_source": ["bucket", "objectKey", "labels"], "query": body
            })
            while True:
                scroll_id = page.get("_scroll_id", scroll_id)
                hits = page["hits"]["hits"]
                if not hits:
                    return results
                results.extend(h["_source"] for h in hits)
                if not scroll_id:
                    raise RuntimeError("Elasticsearch did not provide a scroll ID")
                page = request("/_search/scroll", {"scroll": "1m", "scroll_id": scroll_id})
        finally:
            if scroll_id:
                request("/_search/scroll", {"scroll_id": [scroll_id]}, method="DELETE")

    baseline = documents({"match_all": {}})
    if not baseline:
        raise SystemExit("The photo index is empty. Upload a photo before running this check.")
    labels = sorted({label for doc in baseline for label in doc.get("labels", []) if label})
    checks = [(label, [label]) for label in labels]
    checks += [("show me photos with " + label + " in them", [label]) for label in labels]
    # Group every label at least once to catch partially recognized queries.
    checks += [("show me " + labels[i] + " and " + labels[(i + 1) % len(labels)],
                [labels[i], labels[(i + 1) % len(labels)]]) for i in range(0, len(labels), 2)]
    checks += [("show me architecture and cars", ["architecture", "cars"]),
               ("show me photos with housing and aerial view in them", ["housing", "aerial view"]),
               ("show me horses and cars", ["horses", "cars"])]
    print(f"Checking {len(labels)} indexed labels across {len(checks)} searches.", flush=True)
    failures = []
    for number, (query, keywords) in enumerate(checks, 1):
        expected_docs = documents({"bool": {
            "should": [{"match_phrase": {"labels": word}} for word in keywords],
            "minimum_should_match": 1,
        }})
        expected = {(d["bucket"], d["objectKey"]) for d in expected_docs}
        response = client.invoke(
            FunctionName=args.function,
            Payload=json.dumps({"queryStringParameters": {"q": query}}).encode(),
        )
        payload = json.load(response["Payload"])
        if response.get("FunctionError") or payload.get("statusCode") != 200:
            raise SystemExit("Search failed for " + repr(query) + ". Check the Lambda logs.")
        body = json.loads(payload["body"])
        actual = {photo_id(p["url"]) for p in body["results"]}
        ok = actual == expected
        print(f"{'PASS' if ok else 'FAIL'} {number}/{len(checks)} {query!r}: "
              f"{len(actual)} returned, {len(expected)} expected", flush=True)
        if not ok:
            failures.append({"query": query, "missing": len(expected - actual),
                             "extra": len(actual - expected)})

    after = client.get_function_configuration(FunctionName=args.function)
    if after["CodeSha256"] != before["CodeSha256"]:
        raise SystemExit("Lambda code changed during the check. Rerun after deployment finishes.")
    if failures:
        print("MISMATCHES:", json.dumps(failures))
        raise SystemExit("Some searches did not match Elasticsearch. Share this output before proceeding.")
    print("PASS: All checked searches returned the expected photos.")
    print("No photos were uploaded or changed. Connection credentials were not printed or saved.")


if __name__ == "__main__":
    main()
