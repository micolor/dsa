# Risk Profile and Weekly Review

These are the two capabilities of the proactive assistant's third layer: they
bake your risk boundaries into recommendations and close the week with a
review of past decisions.

## Risk profile

Configuration (environment variables):

```env
AGENT_RISK_PROFILE_HORIZON=medium        # short / medium / long
AGENT_RISK_PROFILE_MAX_DRAWDOWN_PCT=15
AGENT_RISK_PROFILE_MAX_POSITION_PCT=35
AGENT_RISK_PROFILE_NOTES=
# Optional: per-account overrides of the global profile (JSON keyed by account_id;
# currently used for the weekly review's concentration checks)
AGENT_RISK_PROFILE_ACCOUNTS_JSON={"2":{"max_position_pct":60,"horizon":"short"}}
```

Where it applies:

- Single-agent chat system prompt: advice must respect the single-symbol cap
  and drawdown tolerance; when exceeded, prefer trimming/diversifying;
- Event-driven agent analysis: event data carries the profile and conclusions
  respect it;
- Weekly review: checks single-symbol concentration and portfolio drawdown
  against the profile limits;
- Per-account overrides: `AGENT_RISK_PROFILE_ACCOUNTS_JSON` can override the
  single-symbol cap/horizon/drawdown per `account_id`; the weekly review checks
  each account's concentration separately (chat and event analysis still use
  the global profile).

The profile only constrains advice; it never rebalances or trades
automatically.

## Weekly review

A weekly review is generated every Friday 15:30 by default and written to the
proactive inbox (type `weekly`):

- **Account**: total equity, floating P&L, realised P&L;
- **Decision review**: decisions maturing this week, hit/miss/neutral, direction
  hit rate, average return and excess return;
- **Risk profile**: single-symbol concentration and whether portfolio drawdown
  exceeds limits;
- **Watchlist weekly moves**: top 3 gainers/losers (last 5 trading days);
- **Alerts**: count this week and the latest 3;
- **Next week's focus**: derived from profile risks and watchlist movers.

Configuration:

```env
AGENT_WEEKLY_REVIEW_ENABLED=true
AGENT_WEEKLY_REVIEW_DAY=4          # 0=Monday ... 6=Sunday
AGENT_WEEKLY_REVIEW_TIME=15:30
AGENT_WEEKLY_REVIEW_NOTIFY=true
```

- Only one review per ISO week (recorded in `proactive_brief_runs`,
  slot=`weekly`);
- Shares the top-right bell inbox and unread count with the brief and event
  alerts;
- `--serve-only` does not start the scheduler; use `--serve` / `--schedule` or
  the manual endpoints.

## Manual trigger

```bash
curl http://127.0.0.1:8010/api/v1/agent/proactive/weekly/preview
curl -X POST http://127.0.0.1:8010/api/v1/agent/proactive/weekly/run
```

## Limitations

- The decision review only counts signals that already have an outcome; when
  nothing matures it shows "nothing due this week";
- Weekly moves depend on the daily-bar cache; symbols with insufficient data
  are skipped;
- The global profile is a single configuration; per-account overrides currently
  only cover the weekly review's concentration check, while chat and event
  analysis use the global profile;
- The review is auxiliary information, not investment advice.

## Related capabilities

The opportunity scan (candidates and rationale from the screening snapshot and
hotspots) is documented in [opportunity-scan_EN.md](opportunity-scan_EN.md).
