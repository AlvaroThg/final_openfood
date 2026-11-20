# Ciclo de vida de ML

Cómo se construye, evalúa, aprueba y versiona un modelo. Código: paquete `nutriscore` (`ml/nutriscore`).

## 1. Datos

| Paso | Regla | Módulo |
|---|---|---|
| Columnas | Solo las del contrato (`schema.RAW_COLUMNS`, 22 de 163). Excluidas: `nutrition-score-fr_100g` y `nutrition-score-uk_100g` (fuga: el grado se deriva del puntaje por umbrales) y `sodium_100g` (redundante con la sal). | `schema.py` |
| Carga | Por bloques de 50 000 filas, como texto, y conversión explícita a número. | `cleaning.py` |
| Objetivo | `nutrition_grade_fr` → `grade` en mayúsculas. Fila sin grado A–E: se descarta. | `cleaning.py` |
| Fuera de rango | El valor pasa a NaN (es un faltante; no se inventa un valor). | `schema.range_violations` |
| Inconsistencias | Grasas saturadas o trans > grasas; azúcares > carbohidratos; grasas + carbohidratos + proteínas + sal > 105 g. La fila se descarta. Las mismas reglas producen un 400 en la API. | `schema.consistency_violations` |
| Obligatorios | Si falta energía, grasas saturadas, azúcares, proteínas o sal, la fila se descarta. La API siempre recibe esos campos; entrenar con ellos imputados crearía *train/serve skew*. | `cleaning.py` |
| Duplicados | Por `code`, se conserva el último. | `cleaning.finalize` |
| Inmutabilidad | Un dataset procesado (`clean_vN.parquet`) nunca se sobrescribe; su SHA-256 queda registrado. | `scripts/prepare_data.py` |

## 2. Particiones

- La partición es **determinista por hash del código** (`SPLIT_SALT = nutriscore-split-v1`): 70 % train, 15 % validación y 15 % test.
- Un producto queda siempre en la misma partición, también en versiones futuras del dataset. Así ningún producto de test termina en un entrenamiento futuro.
- **Validación**: decide la aprobación.
- **Test**: congelado; solo se reporta y nunca se usa para elegir modelos ni hiperparámetros.
- Para explorar se usa una **muestra estratificada** de 80 000 filas de train. El modelo final se entrena con el split de train completo.

## 3. Modelos

| Configuración | Rol |
|---|---|
| `configs/decision_tree.toml` | Línea base interpretable |
| `configs/random_forest.toml` | Ensamble por bagging |
| `configs/lightgbm.toml` | Candidato a producción |
| `configs/lightgbm_sin_formula.toml` | Experimento 2, solo notebook; no cumple el contrato de la API |

- Todas usan el mismo pipeline: mediana + indicador de faltante, normalización de categorías + one-hot y pesos de clase.
- La configuración (algoritmo, hiperparámetros, semilla y features) se guarda completa en `metadata.json`.

## 4. Política de calidad (`quality.py`, versión `qp-1`)

| Criterio | Umbral | Tipo |
|---|---|---|
| F1 macro (validación) | ≥ 0,80 | Bloqueante |
| Sin regresión | F1 macro ≥ F1 del modelo activo − 0,01 | Bloqueante |
| Recall y precisión por clase | ≥ 0,70 en cada una de A–E | Bloqueante |
| Tamaño del artefacto | ≤ 20 MB | Bloqueante |
| Paridad | Predicciones idénticas tras guardar y recargar | Bloqueante |
| F1 por grupo (`pnns_groups_1`, soporte ≥ 200) | ≥ 0,70 | Advertencia de sesgo |

**Justificación.**

- *F1 macro y métricas por clase*: las clases están desbalanceadas, y un modelo que ignore una clase minoritaria puede tener accuracy alta.
- *Tamaño*: el modelo se sirve en un contenedor con RAM limitada.
- *Paridad*: garantiza que lo que se evaluó es lo que se despliega.

> ⚠️ **Umbrales provisionales.** Se calibran con la línea base del notebook. El suelo de F1 debe quedar por encima del F1 del árbol de decisión. Si cambian, se crea `qp-2`; no se edita `qp-1`. Cada modelo registra la política con la que fue evaluado.

## 5. Estados de un modelo (`lifecycle.py`)

```
candidate → evaluating → approved → active → archived
                 └→ rejected (final)           ↑______|  (rollback)
```

- `retrain_service` mueve un modelo de `candidate` a `approved` o `rejected`.
- Solo `ia_backend` activa un modelo (`approved`/`archived` → `active`); al hacerlo, el activo anterior pasa a `archived`.
- La base de datos garantiza que haya un único modelo activo, y todas las actualizaciones son condicionales al estado esperado.

## 6. Artefactos (`artifacts.py`)

```
models/v1.1.0/  model.joblib · metadata.json · metrics.json · golden.json
```

- **Inmutables**: se escriben en un directorio temporal y se renombran al final; nunca quedan a medio escribir.
- **Hash**: el SHA-256 se verifica **antes** de deserializar, porque `joblib` ejecuta código al cargar.
- **Dependencias**: `scikit-learn`, `lightgbm`, `numpy` y `joblib` deben coincidir exactamente; si no, la carga falla.
- **`golden.json`**: contiene entradas y probabilidades esperadas. `ia_backend` lo verifica al activar un modelo.
- **Separación**: el artefacto guarda los *hechos* del entrenamiento. La *decisión* de la política vive en la base de datos (`model_versions`); `scripts/train.py` la escribe en `vX.Y.Z.gate.json` cuando trabaja sin base de datos.

## 7. Reproducir un modelo fuera de la plataforma

```bash
pip install -e "ml[dev]"
python ml/scripts/prepare_data.py --input data/raw/en.openfoodfacts.org.products.tsv \
    --output data/processed/clean_v1.parquet
python ml/scripts/train.py --config ml/configs/lightgbm.toml --data data/processed/clean_v1.parquet \
    --dataset-version v1 --models-dir models --version 1.0.0
```
