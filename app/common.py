import os, re
from pathlib import Path
ROOT = Path(os.getenv("DATA_ROOT", "/data"))
def case_path(case_id):
    if not re.fullmatch(r"[a-f0-9]{32}", case_id):
        raise ValueError("Invalid case ID")
    return ROOT / case_id
