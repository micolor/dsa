# Event-Driven Agent Analysis

Event-driven analysis is the second layer of the proactive assistant: instead of
waiting for the user or only summarising around market open/close, it runs a
short analysis when something noteworthy happens during the session and leaves
an event alert.

It reuses existing alerts and quote data but calls an **LLM only once** and does
not expose tool calls, so intraday alerts do not turn into another full
multi-agent pipeline.

## Trigger events

- Recently triggered rules from the alert center (filtered by `triggered_at`);
- Position floating loss reaching `-10%`;
- Watchlist/position single-day absolute change `>= 5%`;
- Price breaking below MA20;
- Volume ratio `>= 2`.

The same event type for the same symbol is processed at most once per day;
alert events are deduplicated by trigger id. Each scan processes at most 3
events by default, ordered as alerts → floating loss → large moves → volume
spike → MA20 breakdown.

## Output

Each event produces a 3–5 sentence analysis:

1. What happened and whether it matters;
2. The main risk or opportunity for the trend/position;
3. One action or observation point to watch next.

Results are written to the proactive inbox (type `event`, title such as
"Event alert · time symbol") and the bell shows the unread count. With push
enabled they are also sent to configured channels (currently Feishu etc.).
With `AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY=true`, multiple events from the same
run are merged into one digest push.

## Enabling

```env
AGENT_EVENT_ANALYSIS_ENABLED=true
AGENT_EVENT_ANALYSIS_INTERVAL_MINUTES=10
AGENT_EVENT_ANALYSIS_MAX_PER_RUN=3
AGENT_EVENT_ANALYSIS_NOTIFY=true
AGENT_EVENT_ANALYSIS_TIMEOUT_S=60
# Merge multiple events of one run into a digest push (per-event by default)
AGENT_EVENT_ANALYSIS_DIGEST_NOTIFY=false
# Daily LLM call cap (0 = unlimited; manual runs are not capped)
AGENT_EVENT_ANALYSIS_MAX_PER_DAY=20
# Attach real news to event analysis (skipped when no source is available)
AGENT_EVENT_ANALYSIS_NEWS_ENABLED=true
```

- `AGENT_EVENT_ANALYSIS_ENABLED` is off by default;
- The runtime scheduler scans every `INTERVAL_MINUTES` after waiting one
  interval, so no LLM call happens at startup;
- `AGENT_EVENT_ANALYSIS_NOTIFY` is off by default; enabling it sends real
  notifications;
- With `AGENT_PROACTIVE_MARKET_GUARD=true` (default), event analysis only runs
  on A-share trading days during trading hours and within one hour after close.

## Manual trigger

```bash
# Detect events only; no LLM call, no push
curl http://127.0.0.1:8010/api/v1/agent/proactive/events/preview

# Analyse unprocessed events now (calls the LLM, pushes per config)
curl -X POST http://127.0.0.1:8010/api/v1/agent/proactive/events/run \
  -H 'Content-Type: application/json' -d '{"max_events":1}'
```

## Dedup and limitations

- Dedup state lives in the `event_agent_runs` table with a unique `event_key`;
- If the LLM returns empty content or fails, nothing is stored and the event is
  not marked processed, so it can be retried next round;
- Single analysis is capped at 500 tokens with a 60-second default timeout;
- Event analysis is auxiliary information, not investment advice; it never
  trades automatically or changes positions or alert rules;
- OTC funds have no candles/volume, so default monitoring only makes sense for
  stocks and ETFs;
- `--serve-only` does not start the scheduler; automatic event scans need
  `--serve` / `--schedule`, while manual endpoints are unrestricted.

## Relationship to the daily brief

- Daily brief: pre/post-market summary, "how is today going overall";
- Event-driven analysis: intraday trigger, "what does this mean right now";
- Both write different types into the top-right bell inbox; they never
  overwrite each other and do not enter chat sessions.
