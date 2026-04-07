import json
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from backend.api.models.chat import ChatRequest
from backend.api.services import crew_runner, session_state

router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest):
    state = session_state.get()
    inputs = {**state, "user_question": req.question}

    queue = await crew_runner.run_crew_streaming(inputs)

    async def event_generator():
        while True:
            event = await queue.get()
            yield {"data": json.dumps(event, ensure_ascii=False)}
            if event.get("type") in ("done", "error"):
                break

    return EventSourceResponse(event_generator())
