"""
Bridges synchronous CrewAI with async FastAPI via asyncio.Queue + threading.Thread.

Flow:
  SSE endpoint
    └─ run_crew_streaming(inputs)
         ├─ returns asyncio.Queue immediately
         └─ starts a daemon thread that:
               1. Optionally runs setup_crew() if schema/data files are missing
               2. Starts a poller sub-thread watching stage marker files
               3. Runs crew().kickoff() (blocking)
               4. Puts result/done events into the queue
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# Global lock to prevent concurrent crew executions
# This prevents resource conflicts when multiple crews try to run simultaneously
_crew_execution_lock = threading.Lock()
_crew_execution_active = False

# Stage markers — same as in ui.py
_AGENT_STAGES = [
    {"id": "analyze",    "marker": "output/data_analysis.md"},
    {"id": "visualize",  "marker": "output/visual_report.md"},
    {"id": "synthesize", "marker": "output/final_response.md"},
    {"id": "suggest",    "marker": "output/suggestions.json"},
]


def _reset_crewai_event_context() -> None:
    try:
        from crewai.events import event_context as _ec
        from crewai.events.event_context import EventContextConfig, MismatchBehavior
        _ec._event_context_config.set(
            EventContextConfig(
                mismatch_behavior=MismatchBehavior.SILENT,
                empty_pop_behavior=MismatchBehavior.SILENT,
            )
        )
    except Exception:
        pass


def _clean_stage_outputs() -> None:
    # 1. Clean existing stage markers
    for stage in _AGENT_STAGES:
        p = Path(stage["marker"])
        if p.exists():
            p.unlink()

    # 2. Clean stale charts and metrics
    # We use a hardcoded fallback to 'output' matching main.py logic
    import os
    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    
    charts_dir = output_dir / "charts"
    if charts_dir.exists() and charts_dir.is_dir():
        for f in charts_dir.glob("*.json"):
            try:
                f.unlink()
            except Exception:
                pass
                
    metrics_file = output_dir / "metrics.json"
    if metrics_file.exists():
        try:
            metrics_file.unlink()
        except Exception:
            pass
            
    suggestions_file = output_dir / "suggestions.json"
    if suggestions_file.exists():
        try:
            suggestions_file.unlink()
        except Exception:
            pass


def _start_stage_poller(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    stop_event: threading.Event,
) -> None:
    """Polls marker files and pushes stage events into the queue."""

    def _poll() -> None:
        completed: set[str] = set()
        # Signal first stage starting immediately
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"type": "stage", "stage": "analyze", "status": "started"},
        )
        while not stop_event.is_set():
            for stage in _AGENT_STAGES:
                sid = stage["id"]
                if sid not in completed and Path(stage["marker"]).exists():
                    completed.add(sid)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "stage", "stage": sid, "status": "done"},
                    )
                    # Signal next stage starting
                    stages = [s["id"] for s in _AGENT_STAGES]
                    next_idx = stages.index(sid) + 1
                    if next_idx < len(stages):
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {"type": "stage", "stage": stages[next_idx], "status": "started"},
                        )
            time.sleep(0.4)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()


async def run_crew_streaming(inputs: dict[str, Any]) -> asyncio.Queue:
    """
    Starts a background thread that runs CrewAI, returning an asyncio.Queue
    that yields SSE event dicts until a "done" or "error" event is produced.
    """
    global _crew_execution_active
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    # Check if another crew is already running
    with _crew_execution_lock:
        if _crew_execution_active:
            # Another crew is running, return error immediately
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "error", "message": "Another analysis is already in progress. Please wait."},
            )
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "done"})
            return queue
        _crew_execution_active = True

    def _thread_work() -> None:
        global _crew_execution_active
        try:
            from game_studio.crew import GameStudio
            from backend.api.services.output_reader import (
                read_final_answer,
                read_metrics,
                read_charts,
            )

            _reset_crewai_event_context()

            inputs.setdefault("session_id", "dev")
            inputs.setdefault("max_rows_per_table", 1000)

            studio = GameStudio()

            # The analyltical pipeline (crew) now handles its own schema discovery
            # and data syncing via GetClickHouseSchemaTool and SyncToClickHouseTool. 
            # We no longer need the local file existence logic.

            # Run the main analysis crew (blocking)
            # Retry logic: reasoning models can intermittently exhaust their
            # token budget on thinking, returning empty content. We retry once.
            max_attempts = 2
            last_error = None
            for attempt in range(1, max_attempts + 1):
                # Clean stage marker files so the poller can detect fresh writes
                _clean_stage_outputs()

                # Start stage poller
                stop_polling = threading.Event()
                _start_stage_poller(loop, queue, stop_polling)

                try:
                    studio.crew().kickoff(inputs=inputs)
                    last_error = None
                    stop_polling.set()
                    _reset_crewai_event_context()
                    break  # success
                except ValueError as ve:
                    stop_polling.set()
                    if "None or empty" in str(ve) and attempt < max_attempts:
                        import logging
                        logging.warning(
                            f"LLM returned empty response (attempt {attempt}/{max_attempts}), retrying..."
                        )
                        _reset_crewai_event_context()
                        last_error = ve
                        continue
                    raise
            if last_error:
                raise last_error

            # Read outputs and build result event
            # Read outputs and build result event
            from backend.api.services.output_reader import read_suggestions_json
            answer, md_suggestions = read_final_answer()
            json_suggestions = read_suggestions_json()
            
            # Prefer integrated JSON suggestions from the new task
            final_suggestions = json_suggestions if json_suggestions else md_suggestions
            
            metrics = read_metrics()
            charts = read_charts()
            
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "type": "result",
                    "answer": answer,
                    "metrics": metrics,
                    "charts": charts,
                    "suggestions": final_suggestions,
                },
            )
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "error", "message": str(exc)},
            )
        finally:
            # Release the execution lock so other crews can run
            with _crew_execution_lock:
                _crew_execution_active = False
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "done"})

    thread = threading.Thread(target=_thread_work, daemon=True)
    thread.start()
    return queue


async def run_suggestions(inputs: dict[str, Any]) -> list[str]:
    """
    Runs suggestions_crew in a thread pool executor (not streaming).
    Returns a list of 4 suggestion strings.
    """
    global _crew_execution_active
    loop = asyncio.get_running_loop()

    # Check if another crew is already running - skip suggestions if so
    with _crew_execution_lock:
        if _crew_execution_active:
            # Another crew is running, return empty suggestions silently
            return []
        _crew_execution_active = True

    def _run() -> list[str]:
        global _crew_execution_active
        try:
            from game_studio.crew import GameStudio
            inputs.setdefault("session_id", "dev")
            _reset_crewai_event_context()
            result = GameStudio().suggestions_crew().kickoff(inputs=inputs)
            _reset_crewai_event_context()
            # Result may be a string (JSON array) or a CrewOutput object
            raw = str(result) if result else "[]"
            # Try to parse as JSON array
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [str(s) for s in data]
            except json.JSONDecodeError:
                pass
            # Fallback: split by newlines
            lines = [l.strip().lstrip("-•*").strip() for l in raw.splitlines() if l.strip()]
            return lines[:4]
        finally:
            # Release the execution lock
            with _crew_execution_lock:
                _crew_execution_active = False

    return await loop.run_in_executor(None, _run)


async def run_dashboard_planner(inputs: dict[str, Any]) -> list[dict]:
    """
    Runs dashboard_planner_crew in a thread pool executor.
    Returns a list of widget dicts.
    """
    global _crew_execution_active
    loop = asyncio.get_running_loop()

    # Check if another crew is already running
    with _crew_execution_lock:
        if _crew_execution_active:
            # Another crew is running, return empty result
            return []
        _crew_execution_active = True

    def _run() -> list[dict]:
        global _crew_execution_active
        try:
            from game_studio.crew import GameStudio
            inputs.setdefault("session_id", "dev")
            _reset_crewai_event_context()
            result = GameStudio().dashboard_planner_crew().kickoff(inputs=inputs)
            _reset_crewai_event_context()
            raw = str(result) if result else "[]"
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
            return []
        finally:
            # Release the execution lock
            with _crew_execution_lock:
                _crew_execution_active = False

    return await loop.run_in_executor(None, _run)
