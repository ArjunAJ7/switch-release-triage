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
LLM_TOKEN = open(os.environ.get("LLM_TOKEN_FILE", "/home/codespace/.cf-token")).read().strip()
SERVICE_REPO = os.environ.get("SERVICE_REPO", "/workspaces/switch-release-triage/fixtures/payments-service")
REPRO_CMD = os.environ.get("REPRO_CMD", "python3 -m pytest -x -q")

client = SwitchClient(BRIDGE, AGENT_ID, API_KEY)
state = {"room": None, "last_tb": None, "last_triage": None, "last_repro": None}

import json, time, hashlib
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.seen.json')
def _load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except Exception:
        return set()
_seen = _load_seen()
def _save_seen():
    try:
        json.dump(sorted(_seen)[-1000:], open(SEEN_FILE, 'w'))
    except Exception:
        pass
_reply_times, _llm_times = [], []
MAX_REPLIES_PER_MIN = 5
MAX_LLM_PER_HOUR = 40
def _breaker(times, limit, window):
    now = time.time()
    times[:] = [t for t in times if now - t < window]
    if len(times) >= limit:
        return False
    times.append(now)
    return True

state = {"room": None, "last_tb": None, "last_triage": None}


def answer(room_id, text):
    if not _breaker(_reply_times, MAX_REPLIES_PER_MIN, 60):
        print('[breaker] reply rate limit hit; dropping reply', flush=True)
        return
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
            {"role": "user", "content": f"In one sentence, what likely broke and who should look first? Facts: {summary}"}], account_id=CF_ACCOUNT)
        summary.append(f"- Read: {prose.strip()}")
    except Exception as e:
        print(f"[llm] {e}", flush=True)
    state["last_triage"] = "\n".join(summary)
    answer(room_id, state["last_triage"])


def reproduce(room_id):
    if not state.get("last_tb"):
        answer(room_id, "Nothing to reproduce yet - post a failing deploy log first.")
        return
    r = brain.run_repro(SERVICE_REPO, REPRO_CMD)
    verdict = "reproduced the crash" if r["code"] != 0 else "did not reproduce (exit 0)"
    state['last_repro'] = 'pytest already run: %s (exit %s)' % (verdict, r['code'])
    answer(room_id, f"Ran `{REPRO_CMD}` in the service checkout: {verdict} (exit {r['code']}).\n```\n{r['out'][-900:]}\n```")


def chat(room_id, question, context=""):
    if not _breaker(_llm_times, MAX_LLM_PER_HOUR, 3600):
        print('[breaker] LLM hourly cap hit; skipping chat', flush=True)
        return
    try:
        out = brain.llm(LLM_TOKEN, [
            {"role": "system", "content": "You are release-triage, a deploy triage agent in a team chat room. Be brief, concrete, no fluff. You can act ONLY by posting in this room. Never claim you notified, messaged, assigned, or otherwise contacted anyone - if a person should do something, phrase it as a recommendation or an ask in the room, never as something already done."},
            {"role": "user", "content": (f"My latest triage:\n{state.get('last_triage') or 'none yet'}\n\nReproduction status: {state.get('last_repro') or 'not run yet'}\n\nRecent room messages:\n{context[-1500:]}\n\nQuestion: {question}")}], account_id=CF_ACCOUNT)
        answer(room_id, out.strip())
    except Exception as e:
        answer(room_id, f"My reasoning backend errored ({e}). Try `reproduce` or paste a log.")


def on_event(ev, payload):
    try:
        with open('/tmp/raw_events.jsonl','a') as fh:
            fh.write(__import__('json').dumps({'ev':ev,'payload':payload}, default=str)[:1500] + chr(10))
    except Exception:
        pass
    if ev != "message":
        return
    p = payload.get("payload", {})
    body = (p.get("body") or "").strip()
    room_id = payload.get("room_id")
    if not p.get("addressed") or not body:
        return
    sender = p.get('sender_name') or ''
    mid = p.get('message_id') or payload.get('sequence') or ''
    key = ('mid:' + str(mid)) if mid else 'h:' + hashlib.sha1(((payload.get('room_id') or '') + '|' + sender + '|' + body).encode()).hexdigest()
    if sender == 'release-triage' or p.get('sender_id') == AGENT_ID:
        _seen.add(key); _save_seen(); return
    if key in _seen:
        return
    _seen.add(key); _save_seen()
    if not body.rstrip().endswith('?'):
        nbkey = 'nb:' + hashlib.sha1((sender + '|' + body).encode()).hexdigest()
        if nbkey in _seen:
            print(f"[skip] repeated automated notice from {sender}", flush=True)
            return
        _seen.add(nbkey); _save_seen()
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
