# OpenFood NutriScore

Plataforma que predice el **Nutri-Score (A–E)** de un producto a partir de sus nutrientes. El modelo se entrena con [Open Food Facts](https://www.kaggle.com/datasets/openfoodfacts/world-food-facts).

Trabajo final de Sistemas Inteligentes. Metodología: **CRISP-ML(Q)**.

Todo corre **en local con Docker**, en cualquier computadora (Windows, macOS o Linux). No necesita dominio ni publicación.

## Arquitectura

```
 navegador ──► frontend (Next.js, único puerto: 127.0.0.1:3000)
                  │  proxy con lista blanca de rutas, red interna de Docker
      ┌───────────┼──────────────────────┐
      ▼           ▼                      ▼
 auth_service   ia_backend          retrain_api ──► retrain_worker
 (JWT RS256)    (predicción)        (admin)          (entrena, evalúa,
      │             │  ▲ activación      │            pide activación)
      │             │  └─────────────────┼────────────────┘
      └─────────────┴──────► postgres ◄──┘   (RLS, un usuario por servicio)
```

| Servicio | Responsabilidad |
|---|---|
| `frontend` | Interfaz: login, predicción, historial, modelo, administración |
| `auth_service` | Registro, login, sesiones con refresh rotado, JWT RS256 y JWKS |
| `ia_backend` | Predicción, historial y activación verificada de modelos aprobados |
| `retrain_service` | API (datasets y trabajos) + worker (entrena, evalúa con la política de calidad) |
| `postgres` | Persistencia con RLS y permisos mínimos por servicio |

Detalles: [docs/arquitectura.md](docs/arquitectura.md) · Seguridad: [docs/seguridad.md](docs/seguridad.md) · Reglas del proyecto: [AGENTS.md](AGENTS.md).

## Puesta en marcha

Requisitos: **Docker Desktop** (o Docker Engine con Compose v2) y unos 4 GB de RAM libres.

```bash
git clone https://github.com/AlvaroThg/final_openfood.git
cd final_openfood

# 1. Secretos aleatorios (una sola vez). Muestra el usuario y la contraseña del administrador.
python scripts/init_env.py
#    Sin Python: docker run --rm -v "${PWD}:/w" -w /w python:3.12.13-slim-bookworm python scripts/init_env.py

# 2. Levantar el sistema (la primera vez construye las imágenes: varios minutos).
docker compose up -d --build

# 3. Datos: coloca UNO de estos archivos
#      data/raw/en.openfoodfacts.org.products.tsv     (el TSV de Kaggle, ~1 GB)
#      data/processed/clean_v1.parquet                (el parquet que genera el notebook)

# 4. Primer modelo: prepara y registra el dataset, entrena y activa por el flujo normal.
docker compose --profile bootstrap run --rm bootstrap
```

Después abre **http://localhost:3000**, crea una cuenta y predice. Con la cuenta del administrador aparece además la sección **Administración**, que permite:

- reentrenar;
- activar versiones;
- volver a una versión anterior.

Operación diaria (detener, reentrenar, backups, solución de problemas): [docs/despliegue-local.md](docs/despliegue-local.md).

## Estructura

```
ml/                 paquete nutriscore (contrato de datos, limpieza, entrenamiento, calidad,
                    ciclo de vida, artefactos), configs, scripts y el notebook CRISP-ML(Q)
db/                 roles (initdb) y migraciones Alembic (esquema, permisos, RLS)
services/
  common/           svc_common: logs JSON, request id, errores, JWT/JWKS, rate limit, RLS
  auth_service/     hexagonal: api · application · domain · infrastructure · schemas
  ia_backend/       idem
  retrain_service/  app/ (API) y worker/ (entrenamiento, activación, bootstrap)
frontend/           Next.js (App Router)
tests/              ml · db · auth · ia_backend · retrain · common · contracts · helpers
docs/               arquitectura, seguridad, base de datos, API, ML, model card, ADR
scripts/            init_env.py · backup_db.sh · restore_db.sh
```

## Pruebas

```bash
python -m venv .venv && .venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -e "ml[dev,notebook]"
pip install -c services/constraints.txt fastapi uvicorn pydantic "pyjwt[crypto]" cryptography \
    "pwdlib[argon2]" "psycopg[binary]" psycopg-pool alembic sqlalchemy httpx2 mypy

docker compose -f docker-compose.test.yml up -d --wait    # PostgreSQL efímero para integración
pytest                                                    # unitarias + integración
ruff check . && mypy services/common/svc_common services/auth_service/app \
    services/ia_backend/app services/retrain_service
```

Sin el PostgreSQL de prueba, las pruebas de integración se **omiten con el motivo visible**. En la CI (`REQUIRE_INTEGRATION=1`) fallan.

## Notebook

[`ml/notebooks/nutriscore_crisp_ml_q.ipynb`](ml/notebooks/nutriscore_crisp_ml_q.ipynb) recorre CRISP-ML(Q) con el dataset real:

- análisis exploratorio;
- demostración de la fuga de información;
- comparación de árbol de decisión, Random Forest y LightGBM;
- experimento sin las variables de la fórmula;
- modelo final.

Se puede abrir en Colab: la primera celda clona el repositorio e instala el paquete. Ciclo de vida del modelo: [docs/ml-lifecycle.md](docs/ml-lifecycle.md).
