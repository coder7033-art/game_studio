from fastapi import APIRouter
from backend.api.services import crew_runner, session_state

router = APIRouter()


@router.get("/suggestions")
async def get_suggestions(question: str = ""):
    state = session_state.get()
    inputs = {**state, "user_question": question}
    suggestions = await crew_runner.run_suggestions(inputs)
    return {"suggestions": suggestions}
