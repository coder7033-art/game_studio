from pydantic import BaseModel
from typing import Any


class ChatRequest(BaseModel):
    question: str


class StageEvent(BaseModel):
    type: str = "stage"
    stage: str
    status: str  # "started" | "done"


class ResultEvent(BaseModel):
    type: str = "result"
    answer: str
    metrics: list[dict[str, Any]]
    charts: list[dict[str, Any]]


class DoneEvent(BaseModel):
    type: str = "done"


class ErrorEvent(BaseModel):
    type: str = "error"
    message: str
