"""Triage brain: deterministic log/git analysis + Workers AI for prose.

The deterministic core does the load-bearing work (parse the traceback, find
the breaking commit with `git log -S`, run the repro). Workers AI
(llama-3.1-8b on the free tier) turns findings into room-friendly prose and
answers free-form follow-ups.
"""
import json
import re
import subprocess
import urllib.request

GH_MODEL = "openai/gpt-4o-mini"


def llm(token, messages, max_tokens=512, account_id=None):
    req = urllib.request.Request(
        "https://models.github.ai/inference/chat/completions",
        data=json.dumps({"model": GH_MODEL, "messages": messages,
                         "max_tokens": max_tokens}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"]["content"]


def parse_traceback(log_text):
    """Pull the exception type, message and app frames out of a deploy log."""
    m = re.search(r"Traceback \(most recent call last\):(.+?)^([\w.]+(?:Error|Exception|Exit|Interrupt|Warning)):[ \t]*([^\n]*)",
                  log_text, re.S | re.M)
    if not m:
        return None
    frames = re.findall(r'File "([^"]+)", line (\d+), in (\S+)', m.group(1))
    return {"exc_type": m.group(2), "exc_msg": m.group(3).strip(),
            "frames": [{"file": f, "line": int(l), "func": fn}
                       for f, l, fn in frames]}


def find_breaking_commit(repo_dir, symbol):
    """git log -S: which commit last touched the lines defining `symbol`."""
    try:
        out = subprocess.run(
            ["git", "log", "-S", symbol, "--format=%H%x09%an%x09%ad%x09%s",
             "--date=short", "-n", "3"],
            cwd=repo_dir, capture_output=True, text=True, timeout=20)
        rows = []
        for line in out.stdout.strip().splitlines():
            h, an, ad, s = line.split("\t", 3)
            rows.append({"sha": h[:8], "author": an, "date": ad, "subject": s})
        return rows
    except Exception:
        return []


def run_repro(repo_dir, cmd, timeout=60):
    try:
        p = subprocess.run(cmd, cwd=repo_dir, shell=True,
                           capture_output=True, text=True, timeout=timeout)
        return {"code": p.returncode,
                "out": (p.stdout + p.stderr)[-1800:]}
    except subprocess.TimeoutExpired:
        return {"code": -1, "out": "repro timed out"}
