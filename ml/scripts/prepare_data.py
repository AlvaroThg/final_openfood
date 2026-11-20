"""TSV crudo de Open Food Facts -> parquet canónico limpio + reporte de limpieza.

Uso:
    python ml/scripts/prepare_data.py \
        --input data/raw/en.openfoodfacts.org.products.tsv \
        --output data/processed/clean_v1.parquet

El parquet resultante es lo que se sube a retrain_service (POST /datasets). Los datasets
son inmutables: si el archivo de salida existe, el script se niega a sobrescribirlo.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nutriscore import schema
from nutriscore.artifacts import sha256_file
from nutriscore.cleaning import load_clean_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunksize", type=int, default=50_000)
    args = parser.parse_args(argv)

    if args.output.exists():
        print(f"ERROR: {args.output} ya existe (los datasets son inmutables; use otra versión)",
              file=sys.stderr)
        return 1

    frame, report = load_clean_dataset(args.input, chunksize=args.chunksize)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output, index=False)

    summary = {
        **report.to_dict(),
        "sha256": sha256_file(args.output),
        "class_distribution": frame[schema.TARGET].value_counts().sort_index().to_dict(),
    }
    report_path = args.output.with_suffix(".report.json")
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
