from fastapi import APIRouter
from backend.api.services import crew_runner, session_state

router = APIRouter()


@router.get("/suggestions")
async def get_suggestions(question: str = ""):
    # STRICT GUARD: If any question context exists (even whitespace), return empty.
    # Suggestions for active flows are handled by the main analytical stream.
    if question and question.strip():
        return {"suggestions": []}

    try:
        inputs = {"question": question}
        # Explicitly uses the lightweight suggestions_crew to avoid main-crew overlap
        suggestions = await crew_runner.run_suggestions(inputs)
        return {"suggestions": suggestions}
    except Exception as e:
        print(f"DEBUG: Suggestions router error: {str(e)}")
        return {"suggestions": []}
