"""Paquete compartido del proyecto OpenFood NutriScore.

Módulos:
- schema: contrato de datos (features, rangos, reglas de consistencia). Fuente única.
- cleaning: carga por bloques del TSV de Open Food Facts y limpieza.
- splits: partición determinista train/val/test y muestreo estratificado.
- preprocessing: transformaciones del pipeline (se serializan junto al modelo).
- training: configuración reproducible, construcción y entrenamiento de modelos.
- evaluation: métricas globales, por clase, matriz de confusión y por grupo.
- quality: política de aprobación versionada.
- lifecycle: estados y transiciones de las versiones de modelo.
- artifacts: guardado/carga inmutable de artefactos con verificación de hash.
"""

__version__ = "1.0.0"
