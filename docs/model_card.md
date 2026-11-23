# Model card: clasificador Nutri-Score

> Las cifras provienen de entrenar con el dataset real. La plataforma también las muestra en la sección **Modelo** (`GET /model/metrics`).

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

## Métricas con el dataset real

Entrenado el 23/11/2026 con Open Food Facts. De 356.027 filas leídas quedaron 252.684 tras la limpieza, repartidas en 177.135 para entrenar, 37.989 para validar y 37.560 para test. El detalle completo está en `docs/resultados/metricas_modelos.json`.

| Modelo | F1 macro validación | Accuracy validación | F1 macro test | Tamaño | Política qp-1 |
|---|---|---|---|---|---|
| Árbol de decisión | 0.921 | 0.923 | 0.922 | 0.2 MB | Aprobado |
| Random Forest | 0.888 | 0.892 | 0.891 | 44.9 MB | Rechazado por tamaño |
| LightGBM | 0.979 | 0.980 | 0.979 | 5.7 MB | Aprobado |
| LightGBM sin variables de la fórmula | 0.669 | 0.670 | 0.668 | 5.6 MB | Rechazado |

- **LightGBM por clase en validación.** Precisión y recall superan 0.95 en las cinco clases. La clase más difícil es B, con precisión 0.958 y recall 0.971.
- **Errores.** Casi todos son entre grados vecinos, por ejemplo C confundida con B o con D.
- **Experimento sin la fórmula.** Al quitar energía, grasas saturadas, azúcares, fibra, proteínas, sal y porcentaje de frutas y verduras, el F1 macro baja de 0.979 a 0.669. La diferencia de 0.31 muestra cuánto del resultado viene de aproximar la fórmula.
- **Advertencia de sesgo.** El grupo frutas y verduras obtiene F1 macro 0.679 en validación, por debajo del umbral de 0.70. Es una advertencia, no bloquea la aprobación.
- **Calibración pendiente.** El árbol de decisión de referencia ya supera el suelo de 0.80 de qp-1 por 0.12. El suelo debería subir en una política qp-2, justificada con estos resultados. No se modificó qp-1.

## Limitaciones y riesgos

- **Accuracy inflada:** el Nutri-Score es una fórmula sobre casi las mismas variables, así que el modelo aproxima la fórmula. El experimento sin esas variables cuantifica cuánta señal hay fuera de ella.
- **Sesgo de la fuente:** Open Food Facts es colaborativo y tiene sobrerrepresentación de productos franceses, datos faltantes y errores de carga.
- **Errores esperables:** se concentran entre grados vecinos (B↔C, C↔D).
- **Algoritmo vigente:** el modelo refleja el Nutri-Score del momento en que se recolectaron los datos, no el algoritmo revisado de 2023.
