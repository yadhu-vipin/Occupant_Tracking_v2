import importlib.util
from pathlib import Path

_state_py = Path(__file__).parent.parent / "state.py"
if _state_py.exists():
    _spec = importlib.util.spec_from_file_location("dsts_state_mod", _state_py)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    StateTable = getattr(_mod, "StateTable", None)
