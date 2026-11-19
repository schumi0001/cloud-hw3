#!/usr/bin/env python3
"""Add an optional search-text slot, build the draft, and test text capture."""
import json
import time
import uuid
from pathlib import Path

import boto3


LABELS = [
    "furkanlabel", "architecture", "building", "housing", "house", "grass",
    "plant", "outdoors", "portico", "suburb", "aerial view", "cottage",
    "villa", "door", "lawn", "cougar", "kangaroo", "panther",
]


def pages(client, operation, key, **arguments):
    while True:
        response = getattr(client, operation)(**arguments)
        yield from response.get(key, [])
        token = response.get("nextToken")
        if not token:
            return
        arguments["nextToken"] = token


def writable_fields(client, operation, detail):
    fields = client.meta.service_model.operation_model(operation).input_shape.members
    return {key: value for key, value in detail.items() if key in fields}


def capture(runtime, settings, text):
    response = runtime.recognize_text(
        **settings,
        sessionId=uuid.uuid4().hex,
        text=text,
        sessionState={
            "intent": {
                "name": "SearchIntent",
                "state": "InProgress",
                "slots": {},
            },
            "dialogAction": {"type": "ElicitSlot", "slotToElicit": "RawQuery"},
        },
    )
    intent = response.get("sessionState", {}).get("intent", {})
    slot = (intent.get("slots") or {}).get("RawQuery") or {}
    value = slot.get("value") or {}
    captured = value.get("originalValue") or value.get("interpretedValue") or ""
    return intent.get("name"), captured


def main():
    session = boto3.Session(region_name="us-east-1")
    configuration = session.client("lambda").get_function_configuration(
        FunctionName="search-photos"
    )
    env = configuration["Environment"]["Variables"]
    runtime_settings = {
        "botId": env["LEX_BOT_ID"],
        "botAliasId": env["LEX_BOT_ALIAS_ID"],
        "localeId": env["LEX_LOCALE_ID"],
    }
    models = session.client("lexv2-models")
    alias = models.describe_bot_alias(
        botId=runtime_settings["botId"], botAliasId=runtime_settings["botAliasId"]
    )
    if alias["botVersion"] != "DRAFT":
        raise SystemExit("The live alias uses a numbered version. Stop here and share this message.")

    base = {
        "botId": runtime_settings["botId"], "botVersion": "DRAFT",
        "localeId": runtime_settings["localeId"],
    }
    locale = models.describe_bot_locale(**base)
    if locale["botLocaleStatus"] not in ("Built", "ReadyExpressTesting", "NotBuilt", "Failed"):
        raise SystemExit("The locale is busy or needs attention: " + locale["botLocaleStatus"])
    intents = list(pages(models, "list_intents", "intentSummaries", **base))
    intent = next((i for i in intents if i["intentName"] == "SearchIntent"), None)
    if not intent:
        raise SystemExit("SearchIntent was not found. No changes made.")
    scope = dict(base, intentId=intent["intentId"])
    intent_detail = models.describe_intent(**scope)
    slots = list(pages(models, "list_slots", "slotSummaries", **scope))
    existing = next((s for s in slots if s["slotName"] == "RawQuery"), None)

    if existing:
        detail = models.describe_slot(**scope, slotId=existing["slotId"])
        if (detail["slotTypeId"] != "AMAZON.FreeFormInput"
                or detail["valueElicitationSetting"]["slotConstraint"] != "Optional"):
            raise SystemExit("A different RawQuery slot already exists. No changes made.")
        raw_slot_id = existing["slotId"]
        if "slotCaptureSetting" in detail["valueElicitationSetting"]:
            backup = Path.home() / ("hw3-lex-before-repair-" + uuid.uuid4().hex[:8] + ".json")
            backup.write_text(json.dumps({"intent": intent_detail, "slot": detail}, indent=2, default=str) + "\n")
            arguments = writable_fields(models, "UpdateSlot", detail)
            arguments["valueElicitationSetting"] = dict(detail["valueElicitationSetting"])
            arguments["valueElicitationSetting"].pop("slotCaptureSetting")
            models.update_slot(**dict(arguments, **scope, slotId=raw_slot_id))
            print("Removed next-step settings from optional RawQuery slot.", flush=True)
        print("Using the existing optional RawQuery slot.", flush=True)
    else:
        snapshot = {
            "alias": alias,
            "intent": intent_detail,
            "slots": [models.describe_slot(**scope, slotId=s["slotId"]) for s in slots],
        }
        backup = Path.home() / ("hw3-lex-before-update-" + uuid.uuid4().hex[:8] + ".json")
        backup.write_text(json.dumps(snapshot, indent=2, default=str) + "\n")
        created = models.create_slot(
            **scope,
            slotName="RawQuery",
            description="Capture search text containing labels outside the example vocabulary.",
            slotTypeId="AMAZON.FreeFormInput",
            multipleValuesSetting={"allowMultipleValues": False},
            valueElicitationSetting={
                "slotConstraint": "Optional",
                "promptSpecification": {
                    "messageGroups": [{"message": {"plainTextMessage": {
                        "value": "What would you like to find in your photos?"
                    }}}],
                    "maxRetries": 0,
                    "allowInterrupt": True,
                },
            },
        )
        raw_slot_id = created["slotId"]
        print("Added optional RawQuery slot. Previous settings saved to", backup.name, flush=True)

    priorities = list(intent_detail.get("slotPriorities", []))
    if not any(item["slotId"] == raw_slot_id for item in priorities):
        priorities.append({
            "slotId": raw_slot_id,
            "priority": max((item["priority"] for item in priorities), default=0) + 1,
        })
        arguments = writable_fields(models, "UpdateIntent", intent_detail)
        arguments["slotPriorities"] = priorities
        models.update_intent(**dict(arguments, **scope))
        print("Added RawQuery priority after the existing slots.", flush=True)

    models.build_bot_locale(**base)
    print("Building Lex. This can take a few minutes.", flush=True)
    deadline = time.monotonic() + 600
    while True:
        state = models.describe_bot_locale(**base)
        status = state["botLocaleStatus"]
        if status == "Built":
            break
        if status == "Failed":
            raise SystemExit("Lex build failed: " + json.dumps(state.get("failureReasons", [])))
        if time.monotonic() > deadline:
            raise SystemExit("Build is still running. Run this helper again once the build finishes.")
        time.sleep(5)

    runtime = session.client("lexv2-runtime")
    queries = [q for label in LABELS for q in (label, "show me " + label)]
    queries += [
        "show me architecture and cars",
        "show me photos with housing and aerial view in them",
        "show me horses and cars",
        "furkanlabel1",
        "newlabel_" + uuid.uuid4().hex[:8],
        "I want to see Sam and Sally",
    ]

    # Allow a short propagation window after the build, then report exact failures.
    for attempt in range(6):
        failed = []
        for query in queries:
            intent_name, captured = capture(runtime, runtime_settings, query)
            if intent_name != "SearchIntent" or captured.strip().casefold() != query.strip().casefold():
                failed.append({"query": query, "intent": intent_name, "captured": captured})
        if not failed:
            print(f"PASS: Lex captured all {len(queries)} inputs, including an unseen custom label.")
            print("Lambda code has not been changed. Next step: update search handling and test results.")
            return
        if attempt < 5:
            time.sleep(5)

    for failure in failed:
        print("FAIL:", json.dumps(failure))
    raise SystemExit("The capture test did not pass. Do not update the Lambda yet.")


if __name__ == "__main__":
    main()
