# switch-release-triage

A Switch agent that lives in your release room and triages failed deploys.

When CI posts a failed deploy log, the agent parses the traceback, finds the
commit that introduced the crashing line (`git log -S` against the service
checkout), and posts a triage note back to the room: crash, site, breaking
commit with author, and a one-line assessment. Say `reproduce` and it re-runs
the service's test suite in the checkout and reports whether the crash still
reproduces. Ask it anything else and it answers from room context.

Built for the [Flip the Switch](https://www.wemakedevs.org/hackathons/switch)
hackathon, Track 01 (Best Business Workflow).

## How it works

```
CI pipeline ──post──▶ Switch bridge ──SSE──▶ release-triage agent
                          ▲                      │
                          │                brain.py
                          │              ├─ parse traceback
                          │              ├─ git log -S <crashing line>
                          │              ├─ pytest (on 'reproduce')
                          │              └─ LLM prose (Workers AI)
                          └────triage note / answers────┘
```

- `agent/switch_client.py` - Switch agent protocol client: registration,
  heartbeat, SSE event stream, posting, ops.
- `agent/brain.py` - traceback parsing, breaking-commit search, test-runner
  reproduction, LLM call (Cloudflare Workers AI, `@cf/meta/llama-3.1-8b-instruct`).
- `agent/main.py` - event loop and message routing (deploy notice -> triage,
  `reproduce` -> test run, anything else -> chat).
- `poster/simulate_ci.py` - stand-in CI pipeline: registers a post-only
  `ci-bot` agent and drops a synthetic failed-deploy notice into the room.
- `poster/ask.py` - posts a free-form question to the agent as `ci-bot`.
- `fixtures/build_service_repo.sh` - builds the example `payments-service`
  repo with real history, including the commit that breaks currency
  conversion.

## Run it

Prereqs: a Switch deployment (the standalone compose in
`switch/deploy/local` works), Python 3.10+, and a Cloudflare account with
Workers AI (free tier is fine).

```bash
# 1. example service repo the agent triages against
./fixtures/build_service_repo.sh /tmp/payments-service

# 2. agent configuration
export SWITCH_BRIDGE=http://127.0.0.1:8000
export SWITCH_REG_TOKEN=<AGENT_REGISTRATION_TOKEN from your Switch .env>
export SERVICE_REPO=/tmp/payments-service
export CF_ACCOUNT_ID=<your cloudflare account id>
export LLM_TOKEN_FILE=/path/to/cf-api-token   # Workers AI read token

# 3. go
python3 agent/main.py
```

Then, from any room the agent is in:

```
you:  @release-triage deploy #47 to production FAILED
      ```
      ... traceback ...
      ```
bot:  Deploy failure triage:
      - Crash: ZeroDivisionError: float division by zero
      - Site: /srv/payments/currency.py:9 in convert
      - Breaking change (git log -S convert): `ecab8e90` "support promo exchange rates"
      - Read: promo rate of zero reaches the division unguarded.

you:  reproduce
bot:  Ran `python3 -m pytest -q` in the service checkout: reproduced the
      crash (exit 1). 1 failed, 2 passed.

you:  @release-triage what just broke, and who should look first?
bot:  Deploy #47 failed on a ZeroDivisionError in currency conversion;
      the promo-rates commit is the place to start.
```

## Notes

- The agent only speaks when addressed (`@release-triage`) so it stays quiet
  in a busy room.
- `poster/simulate_ci.py` and the `payments-service` fixture exist so the
  whole loop is reproducible without a real CI system; point a real pipeline
  at the same room and it works unchanged.
- No secrets in the repo: tokens come from the environment or files outside
  the checkout.

## Demo data is synthetic

Every name, log and commit in the demo is invented. The deploy logs come from
`poster/simulate_ci.py`, the `payments-service` repo and its history
(including the author "Dev Malhotra" and the breaking commit) are generated
by `fixtures/build_service_repo.sh`, and the chat participants are throwaway
accounts on a local Mattermost. No real person, employer, repository or
production system is depicted.
