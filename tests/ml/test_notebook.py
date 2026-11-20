"""Prueba de humo del notebook: ejecuta todas sus celdas de código con datos sintéticos.

Detecta celdas rotas por cambios en el paquete `nutriscore` sin descargar el dataset real.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pytest

from tests.helpers.synthetic import make_clean_frame, to_raw_frame

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "ml" / "notebooks" / "nutriscore_crisp_ml_q.ipynb"


@pytest.mark.slow
@pytest.mark.filterwarnings("ignore:FigureCanvasAgg is non-interactive")
def test_notebook_runs_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    raw = tmp_path / "off.tsv"
    to_raw_frame(make_clean_frame(n_rows=6_000, seed=3)).to_csv(raw, sep="\t", index=False)
    monkeypatch.setenv("NUTRISCORE_RAW", str(raw))
    monkeypatch.setenv("NUTRISCORE_PROCESSED", str(tmp_path / "clean_v1.parquet"))
    monkeypatch.setenv("NUTRISCORE_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.chdir(NOTEBOOK.parent)

    namespace: dict[str, object] = {"display": lambda *args: None, "__name__": "__main__"}
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    for index, cell in enumerate(cells):
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), f"<celda {index}>", "exec"), namespace)  # noqa: S102
            plt.close("all")

    assert (tmp_path / "models" / "v1.0.0" / "model.joblib").exists()
    assert (tmp_path / "clean_v1.parquet").exists()
