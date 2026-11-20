"""Entrena una configuración sobre un dataset procesado y guarda la versión de modelo.

Uso:
    python ml/scripts/train.py --config ml/configs/lightgbm.toml \
        --data data/processed/clean_v1.parquet --dataset-version v1 \
        --models-dir models --version 1.0.0

Es el mismo flujo que ejecuta retrain_service/worker, sin base de datos: sirve para
reproducir un modelo fuera del sistema. El resultado de la política de calidad (una
decisión, no un hecho del entrenamiento) se escribe junto al artefacto en
<models-dir>/v<version>.gate.json; en el sistema vive en la tabla model_versions.

Códigos de salida: 0 aprobado · 3 rechazado por la política de calidad · 1 error de entrada.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from nutriscore import schema
from nutriscore.artifacts import read_metrics, reload_parity, save_artifact, sha256_file
from nutriscore.cleaning import validate_clean_frame
from nutriscore.quality import CURRENT_POLICY, evaluate_gate
from nutriscore.splits import split_frame
from nutriscore.training import TrainingConfig, build_metadata, train_and_evaluate

GOLDEN_ROWS = 50
EXIT_REJECTED = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--version", required=True, help="MAJOR.MINOR.PATCH, p. ej. 1.0.0")
    parser.add_argument("--active-version", default=None,
                        help="versión activa contra la que se compara (no regresión)")
    args = parser.parse_args(argv)

    config = TrainingConfig.from_toml(args.config)
    frame = pd.read_parquet(args.data)
    problems = validate_clean_frame(frame)
    if problems:
        print("ERROR: dataset inválido:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1

    splits = split_frame(frame)
    result = train_and_evaluate(config, splits, evaluate_test=True,
                                min_slice_support=CURRENT_POLICY.min_slice_support)
    golden_frame = splits["val"].sample(n=min(GOLDEN_ROWS, len(splits["val"])),
                                        random_state=config.seed)
    metadata = build_metadata(config, result, splits, dataset_version=args.dataset_version,
                              dataset_sha256=sha256_file(args.data),
                              quality_policy_version=CURRENT_POLICY.version)
    info = save_artifact(result.pipeline, args.models_dir, args.version, metadata=metadata,
                         metrics={"validation": result.validation, "test": result.test},
                         golden_frame=golden_frame)

    active = (read_metrics(args.models_dir, args.active_version)["validation"]
              if args.active_version else None)
    gate = evaluate_gate(result.validation, active=active, artifact_bytes=info.size_bytes,
                         parity_ok=reload_parity(result.pipeline, args.models_dir, info,
                                                 golden_frame))
    gate_path = args.models_dir / f"v{args.version}.gate.json"
    gate_path.write_text(json.dumps(gate.to_dict(), indent=2, ensure_ascii=False),
                         encoding="utf-8")

    print(json.dumps({
        "version": info.version,
        "sha256": info.sha256,
        "size_mb": round(info.size_bytes / 1024 / 1024, 2),
        "validation": {k: round(result.validation[k], 4)
                       for k in ("accuracy", "f1_macro", "precision_macro", "recall_macro")},
        "test": {k: round(result.test[k], 4) for k in ("accuracy", "f1_macro")}
        if result.test else None,
        "gate_passed": gate.passed,
        "gate_failed": [c.name for c in gate.failed_blocking],
        "gate_warnings": [c.name for c in gate.warnings],
        "classes": list(schema.GRADES),
    }, indent=2, ensure_ascii=False))
    return 0 if gate.passed else EXIT_REJECTED


if __name__ == "__main__":
    sys.exit(main())
