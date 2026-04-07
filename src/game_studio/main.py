#!/usr/bin/env python
import json
import os
import sqlite3
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from game_studio.crew import GameStudio

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def _base_inputs() -> dict:
    connection_uri = os.getenv("DB_CONNECTION_URI")
    database_type = os.getenv("DB_TYPE", "sqlite")
    user_question = os.getenv("USER_QUESTION")

    if not connection_uri:
        raise ValueError("Missing required environment variable: DB_CONNECTION_URI. Please provide a database connection string.")
    
    if not user_question:
        raise ValueError("Missing required environment variable: USER_QUESTION. Please provide an analytical question.")

    return {
        "database_type": database_type,
        "connection_uri": connection_uri,
        "max_rows_per_table": _safe_int(os.getenv("MAX_ROWS_PER_TABLE"), 1000),
        "user_question": user_question,
        "report_format": os.getenv("REPORT_FORMAT", "report"),
        "session_id": os.getenv("SESSION_ID", "dev"),
    }


def run():
    """Run the crew."""
    inputs = _base_inputs()
    studio = GameStudio()

    try:
        # Analysis phase
        print(f"--- Running Analysis for: {inputs['user_question']} ---")
        studio.crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}")


def train():
    """Train the crew for a given number of iterations."""
    inputs = _base_inputs()

    try:
        GameStudio().crew().train(
            n_iterations=int(sys.argv[1]),
            filename=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")


def replay():
    """Replay the crew execution from a specific task."""
    try:
        GameStudio().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")


def test():
    """Test the crew execution and returns the results."""
    inputs = _base_inputs()

    try:
        GameStudio().crew().test(
            n_iterations=int(sys.argv[1]),
            eval_llm=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")


def run_with_trigger():
    """Run the crew with trigger payload."""
    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument")

    inputs = _base_inputs()
    if isinstance(trigger_payload, dict):
        inputs.update(trigger_payload)
    inputs["crewai_trigger_payload"] = trigger_payload

    try:
        result = GameStudio().crew().kickoff(inputs=inputs)
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the crew with trigger: {e}")
