import os
import sys
from pathlib import Path

os.environ["USE_FIXTURES"] = "1"
os.environ["ASI_ONE_API_KEY"] = ""
os.environ["PUSH_ENABLED"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
