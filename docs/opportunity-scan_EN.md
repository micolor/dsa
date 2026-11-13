# Opportunity Scan

The opportunity scan turns the latest screening snapshot and hot themes into a
candidate list, writes it to the proactive inbox (type `opportunity`), and
marks whether each candidate is already in the watchlist or portfolio.

It is not a real-time recommendation and does not run screening on its own; the
data comes from DSA's existing screening history and hotspot cache.

## Schedule

Defaults to `08:45,15:45` (Asia/Shanghai). The runtime scheduler checks every 5
minutes; only one scan per date + time slot is generated.

## Inputs

- The latest screening run and its candidates (score, theme, rationale, risks,
  watch items);
- The latest hotspot theme cache;
- Current watchlist and positions, used to mark "in watchlist / in portfolio";
- The user risk profile (shown as context only; no automatic filtering here).

## Output

- Candidate list: name/code, score, theme/industry, rationale, risks, watch
  items and holding status;
- Hot themes: change, heat and phase of the latest 3 themes;
- Freshness warnings: screening snapshot or hotspot cache older than the
  threshold is labelled "stale";
- Usage notes and risk disclaimer.

## Configuration

```env
AGENT_OPPORTUNITY_SCAN_ENABLED=true
AGENT_OPPORTUNITY_SCAN_TIMES=08:45,15:45
AGENT_OPPORTUNITY_SCAN_MAX_CANDIDATES=5
AGENT_OPPORTUNITY_SCAN_MAX_AGE_HOURS=72
AGENT_OPPORTUNITY_SCAN_NOTIFY=true
# Re-run screening first when the snapshot is missing or stale (off by default;
# requires network access and takes time)
AGENT_OPPORTUNITY_SCAN_REFRESH=false
```

- Off by default; once enabled locally it generates on schedule;
- `MAX_AGE_HOURS` decides whether the screening snapshot is stale;
- With `NOTIFY` enabled it is pushed to the configured channels.

## Manual trigger

```bash
curl http://127.0.0.1:8010/api/v1/agent/proactive/opportunity/preview
curl -X POST http://127.0.0.1:8010/api/v1/agent/proactive/opportunity/run
```

## Limitations

- Does not run screening automatically; with no screening history it only says
  "run screening first";
- A stale screening snapshot is still shown but labelled; do not place orders
  based on it;
- A stale hotspot cache is labelled the same way;
- Candidates are a research starting point, not a buy recommendation; nothing
  trades automatically;
- `--serve-only` does not start the scheduler; automatic scans need `--serve`
  / `--schedule`, while manual endpoints are unrestricted.
