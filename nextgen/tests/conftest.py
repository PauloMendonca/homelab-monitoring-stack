# conftest.py — pytest configuration for notify_api tests
import sys
from pathlib import Path

# Ensure the notify_api package is importable
notify_api_path = Path(__file__).parent.parent / "notify_api"
sys.path.insert(0, str(notify_api_path.parent))
