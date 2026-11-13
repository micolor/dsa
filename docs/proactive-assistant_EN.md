# Proactive Assistant Overview

The DSA proactive assistant consists of four layers. Their output goes to the
proactive inbox in the top-right bell and is optionally pushed through the
configured notification channels:

| Capability | Inbox type | Trigger | Docs |
| --- | --- | --- | --- |
| Daily brief | `daily_brief` | Pre/post market (default 08:30/15:30) | [proactive-brief_EN.md](proactive-brief_EN.md) |
| Event alerts | `event` | Alerts / floating loss / large moves / volume spikes / MA20 breakdown | [event-driven-agent_EN.md](event-driven-agent_EN.md) |
| Weekly review | `weekly` | Every Friday 15:30 (configurable) | [risk-profile-and-weekly-review_EN.md](risk-profile-and-weekly-review_EN.md) |
| Opportunity scan | `opportunity` | Default 08:45/15:45 | [opportunity-scan_EN.md](opportunity-scan_EN.md) |

## Proactive inbox

The bell in the top-right header aggregates all four proactive message types
into a dropdown (no separate page, no chat session):

- The dropdown shows only unread messages (up to 8); "View all messages" opens a full-screen list;
- The full-screen inbox uses the full page width: each row shows type, title, a one-line summary and time, with type filters, load more, mark all read and mark unread; selecting a message still opens the half-width detail drawer and marks it read;
- Opening an item shows the full Markdown, marks it read automatically and removes it from the new-message list;
- Event messages can be added to the watchlist in one click;
- Read state is stored server-side in the `proactive_inbox_reads` table, so it
  is consistent across browsers and devices;
- The bell badge refreshes every 60 seconds;
- Notification deep links (`AGENT_PROACTIVE_NOTIFY_BASE_URL`) carry
  `?inbox=<id>` and open the matching message automatically.

Proactive messages are no longer written into chat sessions; chat history only
contains conversations between the user and the assistant.

API:

```text
GET  /api/v1/inbox/messages?type=&unread_only=&page=&page_size=&id=
POST /api/v1/inbox/read
POST /api/v1/inbox/unread
POST /api/v1/inbox/read-all
```

## Shared capabilities

- **Risk profile**: `AGENT_RISK_PROFILE_*`; respected by chat, event analysis
  and weekly review;
- **Notification policy**: each capability has its own `*_NOTIFY` switch;
  `AGENT_PROACTIVE_NOTIFY_MIN_SEVERITY` sets the minimum severity,
  `AGENT_PROACTIVE_NOTIFY_ROUTE` selects the route type,
  `AGENT_PROACTIVE_NOTIFY_RETRIES` controls retries, and
  `AGENT_PROACTIVE_NOTIFY_BASE_URL` appends the in-app deep link;
- **Quiet hours**: with `AGENT_PROACTIVE_QUIET_HOURS=22:00-08:00` messages are
  still stored in the inbox but not pushed;
- **Settings page**: all proactive assistant options are available under
  "System settings → Agent settings";
- **Opportunity scan refresh**: when `AGENT_OPPORTUNITY_SCAN_REFRESH=true`, a
  missing or stale snapshot triggers a screening re-run first;
- **Per-account profiles**: `AGENT_RISK_PROFILE_ACCOUNTS_JSON` can override the
  concentration check per `account_id` for the weekly review;
- **Trading-day guard**: with `AGENT_PROACTIVE_MARKET_GUARD=true` (default),
  proactive tasks only run on A-share trading days; event analysis is further
  limited to trading hours plus one hour after close;
- **Cost guard**: `AGENT_EVENT_ANALYSIS_MAX_PER_DAY=20` caps daily event
  analysis LLM calls; 0 means unlimited, manual runs are not capped;
- **Retention**: `AGENT_PROACTIVE_RETENTION_DAYS=90` prunes expired run records
  and read markers; 0 keeps them forever;
- **Independent data refresh**: with `AGENT_DATA_REFRESH_ENABLED=true`, the
  screening snapshot and hotspot cache are refreshed at 08:40/15:40 so the
  opportunity scan can use fresh data;
- **Concurrent scans**: `AGENT_PROACTIVE_SCAN_CONCURRENCY=4` controls how many
  threads read quotes and daily bars for the brief/event scans;
- **Event news**: with `AGENT_EVENT_ANALYSIS_NEWS_ENABLED=true`, event analysis
  includes up to 3 real news items;
- **Inbox actions**: event details can add the stock to the watchlist;
- **Dedup**: daily brief and opportunity scan use date + time slot; weekly
  review uses ISO week; events use the event key;
- **Audit**: daily brief and opportunity scan runs are recorded in
  `proactive_brief_runs`; events in `event_agent_runs`.

## Requirements

- Scheduled tasks need the runtime scheduler: run `main.py --serve` or
  `main.py --schedule`;
- `--serve-only` starts only the web service without the scheduler; use the
  manual endpoints in that mode;
- Manual endpoints live under `/api/v1/agent/proactive/*`:
  - `brief/preview|run`
  - `events/preview|run`
  - `weekly/preview|run`
  - `opportunity/preview|run`

## Safety boundaries

- All content is auxiliary information, not investment advice;
- No automatic trading, no automatic changes to positions, alerts or config;
- High-risk writes still produce a confirmation proposal that the user must
  approve;
- Data source or model failures only degrade the affected section, never the
  main flow;
- Stale data is labelled explicitly (screening snapshot, hotspot cache).
