#!/usr/bin/env bash
# Build the fixture "payments-service" repo with a real history whose last
# feature commit introduces the crash the deploy log shows.
set -euo pipefail
DIR="${1:-payments-service}"
rm -rf "$DIR"; mkdir -p "$DIR"; cd "$DIR"; git init -q -b main

commit() { # msg, author, date
  git add -A
  GIT_AUTHOR_NAME="$2" GIT_AUTHOR_EMAIL="$2@example.com" GIT_AUTHOR_DATE="$3" \
  GIT_COMMITTER_NAME="$2" GIT_COMMITTER_EMAIL="$2@example.com" GIT_COMMITTER_DATE="$3" \
  git commit -q -m "$1"
}

cat > currency.py <<'PY'
RATES = {"USD": 1.0, "EUR": 0.92, "INR": 83.2}

def convert(amount, from_ccy, to_ccy):
    usd = amount / RATES[from_ccy]
    return round(usd * RATES[to_ccy], 2)
PY
commit "add currency converter" "Priya Nair" "2026-10-20T09:12:00"

cat > test_currency.py <<'PY'
from currency import convert

def test_usd_to_eur():
    assert convert(100, "USD", "EUR") == 92.0

def test_inr_roundtrip():
    assert convert(8320, "INR", "USD") == 100.0
PY
commit "add converter tests" "Priya Nair" "2026-10-22T15:40:00"

cat > app.py <<'PY'
from currency import convert

def checkout_total(amount, from_ccy, to_ccy):
    return {"total": convert(amount, from_ccy, to_ccy)}

if __name__ == "__main__":
    print(checkout_total(100, "USD", "EUR"))
PY
commit "add checkout endpoint" "Dev Malhotra" "2026-10-27T11:05:00"

# The breaking change: dynamic rate lookup that can yield a zero rate.
cat > currency.py <<'PY'
RATES = {"USD": 1.0, "EUR": 0.92, "INR": 83.2}
PROMO_RATES = {"FESTIVAL": 0.0}  # zero = campaign not live yet

def convert(amount, from_ccy, to_ccy, promo=None):
    rate = RATES[from_ccy]
    if promo:
        rate = PROMO_RATES.get(promo, rate)
    usd = amount / rate
    return round(usd * RATES[to_ccy], 2)
PY
cat >> test_currency.py <<'PY'

def test_promo_rate_applies():
    assert convert(100, "USD", "EUR", promo="FESTIVAL") >= 0
PY
commit "support promo exchange rates" "Dev Malhotra" "2026-11-02T18:31:00"
echo "fixture repo ready at $DIR"
