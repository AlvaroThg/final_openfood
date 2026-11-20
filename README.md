# OpenFood NutriScore

Plataforma que predice el **Nutri-Score (A–E)** de un producto a partir de sus nutrientes. El modelo se entrena con [Open Food Facts](https://www.kaggle.com/datasets/openfoodfacts/world-food-facts).

Trabajo final de Sistemas Inteligentes. Metodología: **CRISP-ML(Q)**.

> Estado: en construcción. Este README se completa a medida que se agregan los servicios.

## Arquitectura

Son servicios separados que corren en local con Docker Compose, en cualquier computadora con Docker:

| Servicio | Responsabilidad |
|---|---|
| `frontend` | Interfaz (Next.js) |
| `auth_service` | Registro, login, JWT RS256 y JWKS |
| `ia_backend` | Inferencia, historial y activación de modelos |
| `retrain_service` | Datasets, entrenamiento (API + worker) y política de calidad |
| `postgres` | Persistencia con RLS |

Las reglas del proyecto están en [AGENTS.md](AGENTS.md).

## Estructura actual

```
ml/
  nutriscore/   paquete compartido: contrato de datos, limpieza, splits, pipeline,
                entrenamiento, evaluación, política de calidad, ciclo de vida, artefactos
  configs/      configuraciones de entrenamiento versionadas (TOML)
  scripts/      prepare_data.py (TSV → parquet) · train.py (parquet → modelo versionado)
  notebooks/    nutriscore_crisp_ml_q.ipynb
tests/ml/       pruebas del paquete y prueba de humo del notebook
docs/           ml-lifecycle.md
```

## Inicio rápido (ML)

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows  ·  source .venv/bin/activate en Linux/macOS
pip install -e "ml[dev,notebook]"
pytest                          # pytest -m "not slow" para omitir la prueba del notebook
```

Para entrenar con el dataset real, sigue [docs/ml-lifecycle.md](docs/ml-lifecycle.md) §7 o ejecuta el notebook.
