#!/usr/bin/env python3
"""Simulate the CI pipeline posting a failed deploy into the release room.

Registers a throwaway 'ci-bot' agent (post-only) and sends the deploy notice
plus the captured traceback, addressing @release-triage so it picks it up.
"""
import json
import os
import sys
import urllib.request

BRIDGE = os.environ["SWITCH_BRIDGE"]
REG_TOKEN = os.environ["SWITCH_REG_TOKEN"]
ROOM_ID = os.environ["ROOM_ID"]

DEPLOY_LOG = """\
[deploy #47] payments-service -> production
[build] OK (34s)
[migrate] OK (2 migrations)
[smoke] GET /health 200
[smoke] POST /checkout 500 (12ms)
[smoke] checkout smoke failed; rolling back
Traceback (most recent call last):
  File "/srv/payments/app.py", line 7, in checkout_total
    return {"total": convert(amount, from_ccy, to_ccy)}
  File "/srv/payments/currency.py", line 9, in convert
    usd = amount / rate
ZeroDivisionError: float division by zero
[deploy #47] FAILED, rollback complete
"""


def post(url, body, token):
    req = urllib.request.Request(
        BRIDGE + url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def main():
    reg = post("/agents", {
        "name": "ci-bot", "description": "CI pipeline notices",
        "connector_type": "custom",
        "integration_profile": {"connection_model": "always_on",
                                "message_exchange": True,
                                "pre_invocation_mediation": [],
                                "post_invocation_mediation": [],
                                "event_reporting": []},
        "overwrite": True}, REG_TOKEN)
    key = reg["api_key"]
    post(f"/agents/{reg['id']}/message",
         {"room_id": ROOM_ID,
          "content": "@release-triage deploy #47 to production FAILED\n```\n" + DEPLOY_LOG + "\n```"}, key)
    print("deploy notice posted by ci-bot")


if __name__ == "__main__":
    main()
