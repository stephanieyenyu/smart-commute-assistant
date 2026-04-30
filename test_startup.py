import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

try:
    from app.main import app
    print("SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
