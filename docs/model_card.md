# Model card: clasificador Nutri-Score

> Las cifras de esta tarjeta se completan con la ejecución del notebook sobre el dataset real. La plataforma también las muestra en la sección **Modelo** (`GET /model/metrics`).

## Uso previsto

- **Para qué sirve:** estimar el grado Nutri-Score (A–E) de un producto envasado a partir de su información nutricional por 100 g.
- **Uso educativo:** es un trabajo de Sistemas Inteligentes.
- **Qué no es:**
  - no reemplaza el cálculo oficial del Nutri-Score;
  - no es un consejo nutricional;
  - no debe usarse para decisiones de salud o regulatorias.

## Datos

- **Fuente:** Open Food Facts (Kaggle `openfoodfacts/world-food-facts`), unos 356 mil productos.
- **Objetivo:** `nutrition_grade_fr`.
- **Excluidas por fuga de información:** `nutrition-score-fr_100g` y `nutrition-score-uk_100g`. El grado se deriva de ellas por umbrales.
- **Features:** 18 numéricas por 100 g y 2 categorías PNNS, definidas en `ml/nutriscore/schema.py`.
- **Limpieza:**
  - valores fuera de rango físico → faltante;
  - filas físicamente inconsistentes → se descartan;
  - filas sin los 5 nutrientes obligatorios de la API → se descartan;
  - productos duplicados por código → se deduplican.
- **Particiones:** deterministas por hash del código del producto: 70 % train, 15 % validación (decide la aprobación) y 15 % test (congelado).

## Modelo

| Campo | Valor |
|---|---|
| Algoritmo de producción | LightGBM con pesos de clase balanceados (`ml/configs/lightgbm.toml`), seed 42 |
| Líneas base | Árbol de decisión y Random Forest |
| Preprocesamiento | Mediana + indicador de faltante; categorías normalizadas + one-hot con frecuencia mínima |
| Artefacto | `models/vX.Y.Z/` inmutable, con hash, metadata, métricas, golden y versiones exactas de las dependencias |

## Métricas (completar con el notebook)

| Modelo | F1 macro (val.) | Accuracy (val.) | F1 macro (test) |
|---|---|---|---|
| Árbol de decisión | ___ | ___ | ___ |
| Random Forest | ___ | ___ | ___ |
| LightGBM | ___ | ___ | ___ |
| LightGBM sin variables de la fórmula | ___ | ___ | ___ |

- **Política de aprobación `qp-1`:** ver `docs/ml-lifecycle.md` §4.
- **Rendimiento por grupo de alimento (`pnns_groups_1`):** se reporta como advertencia de sesgo.

## Limitaciones y riesgos

- **Accuracy inflada:** el Nutri-Score es una fórmula sobre casi las mismas variables, así que el modelo aproxima la fórmula. El experimento sin esas variables cuantifica cuánta señal hay fuera de ella.
- **Sesgo de la fuente:** Open Food Facts es colaborativo y tiene sobrerrepresentación de productos franceses, datos faltantes y errores de carga.
- **Errores esperables:** se concentran entre grados vecinos (B↔C, C↔D).
- **Algoritmo vigente:** el modelo refleja el Nutri-Score del momento en que se recolectaron los datos, no el algoritmo revisado de 2023.
