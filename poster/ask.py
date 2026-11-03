#!/usr/bin/env python3
"""Post a free-form question to @release-triage as the ci-bot agent."""
import json, os, sys, urllib.request

BRIDGE = os.environ["SWITCH_BRIDGE"]
REG_TOKEN = os.environ["SWITCH_REG_TOKEN"]
ROOM_ID = os.environ["ROOM_ID"]
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "what just broke, and who should look first?"


def post(url, body, token):
    req = urllib.request.Request(
        BRIDGE + url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


reg = post("/agents", {
    "name": "ci-bot", "description": "CI pipeline notices",
    "connector_type": "custom",
    "integration_profile": {"connection_model": "always_on",
                            "message_exchange": True,
                            "pre_invocation_mediation": [],
                            "post_invocation_mediation": [],
                            "event_reporting": []},
    "overwrite": True}, REG_TOKEN)
post(f"/agents/{reg['id']}/message",
     {"room_id": ROOM_ID, "content": f"@release-triage {QUESTION}"},
     reg["api_key"])
print("question posted:", QUESTION)
