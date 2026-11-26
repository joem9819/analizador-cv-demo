"""Demostración para hospedar en Streamlit Community Cloud."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analizador_cv.app import main  # noqa: E402

main(publica=True)
