import json
import os
from datetime import datetime
from threading import Lock

LOG_FILE = "output/llm_debug.jsonl"
_lock = Lock()

def log_llm_call(model, messages, response, usage=None, metadata=None):
    """
    Logs an LLM call to a persistent JSONL file (Append-only for performance).
    """
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    
    entry = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "prompt": messages,
        "response": response,
        "usage": usage or {},
        "metadata": metadata or {}
    }
    
    with _lock:
        try:
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"FAILED TO LOG LLM CALL: {e}")

def get_llm_logs():
    """Reads and returns the current LLM logs from JSONL file."""
    if not os.path.exists(LOG_FILE):
        return []
    with _lock:
        try:
            logs = []
            with open(LOG_FILE, "r") as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
            
            # Return only last 50 calls for UI performance
            return logs[-50:]
        except Exception:
            return []

def clear_llm_logs():
    """Wipes the LLM log file."""
    with _lock:
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
