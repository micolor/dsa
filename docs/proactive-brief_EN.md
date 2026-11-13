# Proactive Daily Brief

The proactive daily brief turns positions, watchlist, alerts and the market
into one readable assistant message instead of waiting for the user to ask.
It complements the existing scheduled analysis, alerts and event monitoring. It
does not call an LLM, so results are repeatable and auditable.

## Behaviour

- Generated at `AGENT_PROACTIVE_BRIEF_TIMES` (default `08:30,15:30`,
  Asia/Shanghai);
- Written to the proactive inbox with type `daily_brief` and a title such as
  "Daily brief · YYYY-MM-DD HH:MM";
- Only one message per date + time slot (recorded in `proactive_brief_runs`
  with a unique key of date + slot);
- Manual runs bypass dedup and can be repeated;
- A single failing data source or symbol only affects its section and never
  blocks the whole brief;
- Generated items show in the top-right bell unread count and open in the
  inbox;
- With `AGENT_PROACTIVE_BRIEF_NOTIFY=true` the brief is also pushed through the
  configured channels (subject to minimum severity, quiet hours and retries).

## Contents

- **Market**: major indices such as SSE, SZSE, ChiNext and SSE 50;
- **Positions**: last price, change, P&L and whether price is above/below MA20;
- **Watchlist moves**: only symbols with |change| ≥ 5%, MA20 breakdown or
  volume ratio ≥ 2;
- **Alerts**: alerts triggered in the last 24 hours (up to 5);
- **Today's focus**: action items derived from positions/watchlist/alerts, such
  as MA20 breakdown, single-day drop, loss over 10% or volume spike.

## Enabling

```env
AGENT_PROACTIVE_BRIEF_ENABLED=true
AGENT_PROACTIVE_BRIEF_TIMES=08:30,15:30
# Maximum watchlist symbols to check; positions are not limited
AGENT_PROACTIVE_BRIEF_WATCHLIST_LIMIT=20
# Push after generation to configured channels (off by default)
AGENT_PROACTIVE_BRIEF_NOTIFY=true
```

> The proactive brief needs the runtime scheduler. `main.py --serve-only` starts
> only the web service without the scheduler; use `main.py --serve` or
> `--schedule`, or the manual endpoints below.
> Docker / long-running deployments usually use `--serve`.

## Manual trigger

```bash
# Preview without writing to the inbox
curl http://127.0.0.1:8000/api/v1/agent/proactive/brief/preview

# Generate and write to the proactive inbox
curl -X POST http://127.0.0.1:8000/api/v1/agent/proactive/brief/run \
  -H 'Content-Type: application/json' -d '{"slot":"manual"}'
```

## Data and limitations

- Depends on real-time quotes, daily-bar cache, position snapshots and the
  alert center; unavailable sources show "temporarily unavailable" in their
  section.
- OTC funds currently show position P&L only; stock MA20/volume logic does not
  apply.
- The brief is auxiliary information, not investment advice; it never trades
  automatically or changes positions or alert rules.
