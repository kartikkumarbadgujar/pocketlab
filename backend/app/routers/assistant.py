"""PocketLab Assistant: a chat endpoint backed by Google Gemini.

The model only sees the signed-in user's own workspace data (projects, recent runs and, when a run is open, that run's
findings and metrics), and is told to answer from that data rather than invent measurements.
"""
from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import current_user
from app.models import Finding, MetricSample, Project, TestRun, User

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
logger = logging.getLogger("pocketlab.assistant")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are PocketLab Assistant, built into PocketLab: a platform that tests Android apps under
entry-level device conditions (low RAM, little storage, weak Indian mobile networks, low battery, heat).

How PocketLab works: users create projects, upload an APK (it stays quarantined until approved), pick condition
profiles and test scenarios in the Test Lab, and start a run. A separate Android worker (emulator or physical device)
executes the run and reports metrics and findings. Measurements are labelled ACTUAL, EMULATED, UNSUPPORTED,
UNAVAILABLE or INVALID; some conditions need a physical device or a Linux network gateway.

Rules:
- Answer from the WORKSPACE DATA below and general Android engineering knowledge. Never invent run results,
  metrics, findings or numbers that are not in the data. If data is missing, say so and explain how to get it.
- When explaining findings, give the likely cause and concrete fixes (code-level where possible).
- Be concise and practical. Use short paragraphs or bullet lists. Use Markdown sparingly."""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    run_id: int | None = None


def _workspace_context(db: Session, user: User, run_id: int | None) -> str:
    projects = db.scalars(select(Project).where(Project.owner_user_id == user.id).order_by(Project.id.desc()).limit(20)).all()
    project_ids = [p.id for p in projects]
    lines = ["WORKSPACE DATA", f"Projects ({len(projects)}):"]
    lines += [f"- #{p.id} {p.name} (package: {p.application_package or 'not set'})" for p in projects] or ["- none"]
    runs = db.scalars(select(TestRun).where(TestRun.project_id.in_(project_ids)).order_by(TestRun.id.desc()).limit(10)).all() if project_ids else []
    lines.append(f"Recent test runs ({len(runs)}):")
    lines += [f"- Run #{r.id} project #{r.project_id}: {r.status}, mode {r.execution_mode}" + (f", error: {r.error_message[:200]}" if r.error_message else "") for r in runs] or ["- none"]
    if run_id is not None:
        run = db.scalar(select(TestRun).where(TestRun.id == run_id, TestRun.project_id.in_(project_ids))) if project_ids else None
        if run is None:
            lines.append(f"The user is viewing run #{run_id}, which is not in their workspace.")
        else:
            lines.append(f"The user is viewing run #{run.id} (status {run.status}).")
            findings = db.scalars(select(Finding).where(Finding.test_run_id == run.id).limit(30)).all()
            lines.append(f"Findings for run #{run.id} ({len(findings)}):")
            lines += [f"- [{f.severity}] {f.title}: {(f.description or '')[:300]}"
                      + (f" | expected: {f.expected_behavior[:150]}" if f.expected_behavior else "")
                      + (f" | actual: {f.actual_behavior[:150]}" if f.actual_behavior else "") for f in findings] or ["- none"]
            metrics = db.scalars(select(MetricSample).where(MetricSample.test_run_id == run.id).order_by(MetricSample.id.desc()).limit(40)).all()
            lines.append(f"Latest metrics for run #{run.id} ({len(metrics)} shown):")
            for m in metrics:
                value = m.numeric_value if m.numeric_value is not None else (m.text_value if m.text_value is not None else m.boolean_value)
                lines.append(f"- {m.parameter_key} = {value}{(' ' + m.unit) if m.unit else ''} [{m.validity_status}, source {m.source}]")
    return "\n".join(lines)


@router.get("/status")
def assistant_status(user: User = Depends(current_user)):
    return {"enabled": bool(settings.gemini_api_key), "model": settings.gemini_model if settings.gemini_api_key else None}


@router.post("/chat")
def chat(data: ChatRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not settings.gemini_api_key:
        raise HTTPException(503, "The AI assistant is not configured on this server (GEMINI_API_KEY is not set)")
    if data.messages[-1].role != "user":
        raise HTTPException(422, "The last message must come from the user")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + "\n\n" + _workspace_context(db, user, data.run_id)}]},
        "contents": [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]} for m in data.messages],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1500},
    }
    try:
        response = httpx.post(GEMINI_URL.format(model=settings.gemini_model), json=body, timeout=50,
                              headers={"x-goog-api-key": settings.gemini_api_key})
    except httpx.HTTPError as exc:
        logger.warning("assistant_upstream_unreachable", extra={"error": exc.__class__.__name__})
        raise HTTPException(502, "The AI service could not be reached. Try again in a moment.") from exc
    if response.status_code == 429:
        raise HTTPException(429, "The AI assistant is busy (rate limit reached). Try again in a minute.")
    if response.status_code != 200:
        logger.warning("assistant_upstream_error", extra={"status_code": response.status_code, "body": response.text[:300]})
        raise HTTPException(502, f"The AI service returned an error (HTTP {response.status_code}).")
    payload = response.json()
    candidates = payload.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else None
    text = "".join(p.get("text", "") for p in parts or [] if not p.get("thought"))
    if not text.strip():
        reason = candidates[0].get("finishReason") if candidates else (payload.get("promptFeedback") or {}).get("blockReason")
        raise HTTPException(502, f"The AI service returned no answer ({reason or 'empty response'}).")
    return {"reply": text.strip(), "model": settings.gemini_model}
