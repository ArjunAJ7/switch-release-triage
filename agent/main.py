#!/usr/bin/env python3
"""release-triage: a Switch agent that lives in the release room.

Watches for deploy logs and questions addressed to it, finds the commit that
broke the deploy, and reproduces the failure on request.
"""
import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(__file__))
from switch_client import SwitchClient
import brain

BRIDGE = os.environ["SWITCH_BRIDGE"]
AGENT_ID = os.environ["SWITCH_AGENT_ID"]
API_KEY = os.environ["SWITCH_AGENT_KEY"]
CF_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "")
LLM_TOKEN = open(os.environ.get("LLM_TOKEN_FILE", "/home/codespace/.gh-models-token")).read().strip()
SERVICE_REPO = os.environ.get("SERVICE_REPO", "/workspaces/switch-release-triage/fixtures/payments-service")
REPRO_CMD = os.environ.get("REPRO_CMD", "python3 -m pytest -x -q")

client = SwitchClient(BRIDGE, AGENT_ID, API_KEY)
state = {"room": None, "last_tb": None}


def answer(room_id, text):
    client.post_message(room_id, text)
    print(f"[posted] {text[:90]!r}", flush=True)


def triage(room_id, log_text):
    tb = brain.parse_traceback(log_text)
    if not tb:
        answer(room_id, "I read the log but found no Python traceback. Paste the failing section and I'll dig in.")
        return
    state["last_tb"] = tb
    leaf = tb["frames"][-1] if tb["frames"] else None
    commits = brain.find_breaking_commit(SERVICE_REPO, leaf["func"]) if leaf else []
    summary = [
        f"Deploy failure triage:",
        f"- Crash: `{tb['exc_type']}: {tb['exc_msg'][:140]}`",
    ]
    if leaf:
        summary.append(f"- Site: `{leaf['file']}:{leaf['line']}` in `{leaf['func']}()`")
    if commits:
        c = commits[0]
        summary.append(f"- Breaking change (git log -S {leaf['func']}): `{c['sha']}` \"{c['subject']}\" by {c['author']} on {c['date']}")
    summary.append("Say `@release-triage reproduce` and I'll run the failing path and post the output.")
    try:
        prose = brain.llm(LLM_TOKEN, [
            {"role": "system", "content": "You are a release triage assistant. One or two plain sentences, no markdown."},
            {"role": "user", "content": f"In one sentence, what likely broke and who should look first? Facts: {summary}"}])
        summary.append(f"- Read: {prose.strip()}")
    except Exception as e:
        print(f"[llm] {e}", flush=True)
    answer(room_id, "\n".join(summary))


def reproduce(room_id):
    if not state.get("last_tb"):
        answer(room_id, "Nothing to reproduce yet - post a failing deploy log first.")
        return
    r = brain.run_repro(SERVICE_REPO, REPRO_CMD)
    verdict = "reproduced the crash" if r["code"] != 0 else "did not reproduce (exit 0)"
    answer(room_id, f"Ran `{REPRO_CMD}` in the service checkout: {verdict} (exit {r['code']}).\n```\n{r['out'][-900:]}\n```")


def chat(room_id, question, context=""):
    try:
        out = brain.llm(LLM_TOKEN, [
            {"role": "system", "content": "You are release-triage, a deploy triage agent in a team chat room. Be brief, concrete, no fluff."},
            {"role": "user", "content": f"Room context:\n{context[-2000:]}\n\nQuestion: {question}"}])
        answer(room_id, out.strip())
    except Exception as e:
        answer(room_id, f"My reasoning backend errored ({e}). Try `reproduce` or paste a log.")


def on_event(ev, payload):
    if ev != "message":
        return
    p = payload.get("payload", {})
    body = (p.get("body") or "").strip()
    room_id = payload.get("room_id")
    if not p.get("addressed") or not body:
        return
    print(f"[msg] {p.get('sender_name')}: {body[:100]!r}", flush=True)
    low = body.lower()
    if "traceback" in body or "deploy" in low and ("fail" in low or "error" in low or "crash" in low):
        triage(room_id, body)
    elif "reproduce" in low or "repro" in low:
        reproduce(room_id)
    else:
        try:
            ctx = client.op("read_context", {"room_id": room_id, "limit": 20})
            chat(room_id, body, str(ctx.get("result", "")))
        except Exception:
            chat(room_id, body)


if __name__ == "__main__":
    rooms = [r for r in os.environ.get("ROOMS", "").split(",") if r] or None
    print(f"release-triage online, bridge={BRIDGE}", flush=True)
    client.run(on_event, rooms=rooms)
