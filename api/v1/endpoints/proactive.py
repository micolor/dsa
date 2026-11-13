# -*- coding: utf-8 -*-
"""Proactive assistant brief endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.services.event_analysis_service import EVENT_SESSION_ID, EventAnalysisService
from src.services.opportunity_scan_service import OPPORTUNITY_SESSION_ID, OpportunityScanService
from src.services.proactive_brief_service import BRIEF_SESSION_ID, ProactiveBriefService
from src.services.proactive_data_refresh_service import ProactiveDataRefreshService
from src.services.proactive_status_service import ProactiveStatusService
from src.services.weekly_review_service import WEEKLY_SESSION_ID, WeeklyReviewService

router = APIRouter()


class BriefRunRequest(BaseModel):
    slot: str = "manual"


class EventRunRequest(BaseModel):
    max_events: Optional[int] = None


def _serialize_brief(brief) -> Dict[str, Any]:
    return {
        "generated_at": brief.generated_at.isoformat(),
        "slot": brief.slot,
        "summary": brief.summary,
        "highlights": brief.highlights,
        "markdown": brief.markdown,
        "marker": brief.marker,
    }


@router.get("/brief/preview")
def preview_brief(slot: str = "manual") -> Dict[str, Any]:
    """Generate a brief without writing it into the chat session."""
    try:
        brief = ProactiveBriefService().generate(slot=slot)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to build proactive brief: {exc}") from exc
    return {"status": "ok", **_serialize_brief(brief)}


@router.post("/brief/run")
def run_brief(request: Optional[BriefRunRequest] = None) -> Dict[str, Any]:
    """Generate a brief and persist it as an assistant message."""
    slot = (request.slot if request is not None else "manual") or "manual"
    try:
        result = ProactiveBriefService().run(slot=slot)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to persist proactive brief: {exc}") from exc
    return {
        "status": "ok",
        "session_id": BRIEF_SESSION_ID,
        "message_id": result.message_id,
        "skipped": result.skipped,
        "brief": _serialize_brief(result.brief) if result.brief is not None else None,
    }


def _serialize_event(event) -> Dict[str, Any]:
    return {
        "event_key": event.event_key,
        "stock_code": event.stock_code,
        "event_type": event.event_type,
        "title": event.title,
        "detail": event.detail,
        "severity": event.severity,
        "triggered_at": event.triggered_at,
        "snapshot": event.snapshot,
    }


@router.get("/events/preview")
def preview_events() -> Dict[str, Any]:
    """Detect event-driven candidates without calling the LLM."""
    try:
        events = EventAnalysisService().collect_events(lookback_minutes=24 * 60)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to detect events: {exc}") from exc
    return {"status": "ok", "events": [_serialize_event(event) for event in events]}


@router.post("/events/run")
def run_events(request: Optional[EventRunRequest] = None) -> Dict[str, Any]:
    """Analyse pending events now. This calls the configured LLM and may push notifications."""
    max_events = request.max_events if request is not None else None
    try:
        results = EventAnalysisService().run_once(force=True, max_events=max_events)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to analyse events: {exc}") from exc
    return {
        "status": "ok",
        "session_id": EVENT_SESSION_ID,
        "count": len(results),
        "results": [
            {
                "event": _serialize_event(result.event),
                "content": result.content,
                "message_id": result.message_id,
                "notified": result.notified,
            }
            for result in results
        ],
    }


def _serialize_weekly(review) -> Dict[str, Any]:
    return {
        "generated_at": review.generated_at.isoformat(),
        "week_key": review.week_key,
        "summary": review.summary,
        "markdown": review.markdown,
        "message_id": review.message_id,
        "notified": review.notified,
    }


@router.get("/weekly/preview")
def preview_weekly_review() -> Dict[str, Any]:
    try:
        review = WeeklyReviewService().generate()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to build weekly review: {exc}") from exc
    return {"status": "ok", **_serialize_weekly(review)}


@router.post("/weekly/run")
def run_weekly_review() -> Dict[str, Any]:
    try:
        review = WeeklyReviewService().run(force=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to persist weekly review: {exc}") from exc
    return {
        "status": "ok",
        "session_id": WEEKLY_SESSION_ID,
        "weekly": _serialize_weekly(review) if review is not None else None,
    }


def _serialize_opportunity(scan) -> Dict[str, Any]:
    return {
        "generated_at": scan.generated_at.isoformat(),
        "slot": scan.slot,
        "summary": scan.summary,
        "markdown": scan.markdown,
        "message_id": scan.message_id,
        "notified": scan.notified,
    }


@router.get("/opportunity/preview")
def preview_opportunity_scan() -> Dict[str, Any]:
    try:
        scan = OpportunityScanService().generate(slot="manual")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to build opportunity scan: {exc}") from exc
    return {"status": "ok", **_serialize_opportunity(scan)}


@router.post("/opportunity/run")
def run_opportunity_scan() -> Dict[str, Any]:
    try:
        scan = OpportunityScanService().run(slot="manual", force=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to persist opportunity scan: {exc}") from exc
    return {
        "status": "ok",
        "session_id": OPPORTUNITY_SESSION_ID,
        "scan": _serialize_opportunity(scan) if scan is not None else None,
    }


@router.get("/status")
def proactive_status() -> Dict[str, Any]:
    return {"status": "ok", **ProactiveStatusService().get_status()}


@router.post("/data-refresh/run")
def run_data_refresh() -> Dict[str, Any]:
    result = ProactiveDataRefreshService().run(force=True)
    return {"status": "ok", "result": result}
