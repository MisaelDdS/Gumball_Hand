"""Claude Code hook for the Gumball pet: `on` (UserPromptSubmit) marks this session busy, `off` (Stop) clears it."""
import json
import sys
from pathlib import Path

d = Path(__file__).resolve().parent / "gumball" / "claude_busy"  # same folder pet.py watches
d.mkdir(parents=True, exist_ok=True)
f = d / json.load(sys.stdin).get("session_id", "unknown")
f.touch() if sys.argv[1] == "on" else f.unlink(missing_ok=True)
