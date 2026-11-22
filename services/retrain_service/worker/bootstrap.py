"""Puesta en marcha inicial: deja el sistema con un modelo activo usando el flujo normal.

    docker compose run --rm bootstrap

1. Prepara el dataset en el volumen de datasets, desde la carpeta ./data del host:
       ./data/processed/clean_v1.parquet (p. ej. descargado de Colab), o si no existe,
       ./data/raw/en.openfoodfacts.org.products.tsv (se limpia aquí con nutriscore.cleaning).
2. Inicia sesión como el administrador inicial (ADMIN_EMAIL / ADMIN_PASSWORD).
3. Registra el dataset (POST /retrain/datasets) y encola un entrenamiento (POST /retrain/jobs).
4. Espera a que el worker termine y muestra el resultado (versión, aprobación, activación).

No hay atajos: el modelo pasa por la misma política de calidad y la misma activación verificada
que cualquier reentrenamiento. Si ya hay un modelo activo, no hace nada (salvo --force).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nutriscore import schema
from nutriscore.artifacts import sha256_file
from nutriscore.cleaning import load_clean_dataset

RAW_FILE = "en.openfoodfacts.org.products.tsv"


def log(message: str) -> None:
    print(f"[bootstrap] {message}", flush=True)


def prepare_dataset(file_name: str, datasets_dir: Path, import_dir: Path) -> Path:
    target = datasets_dir / file_name
    if target.is_file():
        log(f"Dataset ya presente: {target}")
        return target
    datasets_dir.mkdir(parents=True, exist_ok=True)
    processed = import_dir / "processed" / file_name
    raw = import_dir / "raw" / RAW_FILE
    if processed.is_file():
        log(f"Copiando {processed} al volumen de datasets")
        shutil.copyfile(processed, target)
        report = processed.with_suffix(".report.json")
        if report.is_file():
            shutil.copyfile(report, target.with_suffix(".report.json"))
        return target
    if raw.is_file():
        log(f"Limpiando {raw} (puede tardar unos minutos)")
        frame, report_data = load_clean_dataset(raw)
        tmp = target.with_suffix(".tmp")
        frame.to_parquet(tmp, index=False)
        tmp.rename(target)
        summary = {
            **report_data.to_dict(),
            "sha256": sha256_file(target),
            "class_distribution": frame[schema.TARGET].value_counts().sort_index().to_dict(),
        }
        target.with_suffix(".report.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log(f"Dataset limpio: {len(frame):,} filas")
        return target
    raise SystemExit(
        f"No hay datos. Coloca {RAW_FILE} en ./data/raw/ o {file_name} en ./data/processed/ "
        "y vuelve a ejecutar."
    )


def call(
    method: str, url: str, payload: dict[str, Any] | None = None, token: str | None = None
) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def wait_for(url: str, seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            status, _ = call("GET", url)
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"{url} no respondió en {seconds} s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", default="clean_v1.parquet")
    parser.add_argument("--config", default="lightgbm")
    parser.add_argument(
        "--force", action="store_true", help="entrenar aunque ya haya modelo activo"
    )
    args = parser.parse_args(argv)

    auth = os.environ.get("AUTH_URL", "http://auth_service:8000").rstrip("/")
    ia = os.environ.get("IA_URL", "http://ia_backend:8000").rstrip("/")
    retrain = os.environ.get("RETRAIN_API_URL", "http://retrain_api:8000").rstrip("/")
    email, password = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Faltan ADMIN_EMAIL y ADMIN_PASSWORD (las del administrador inicial)")

    prepare_dataset(
        args.file,
        Path(os.environ.get("DATASETS_DIR", "/datasets")),
        Path(os.environ.get("IMPORT_DIR", "/import")),
    )

    for url in (f"{auth}/health", f"{ia}/health", f"{retrain}/health"):
        wait_for(url)
    status, body = call("POST", f"{auth}/auth/login", {"email": email, "password": password})
    if status != 200:
        raise SystemExit(f"No se pudo iniciar sesión como administrador: {body}")
    token = body["access_token"]

    status, _ = call("GET", f"{ia}/model/info", token=token)
    if status == 200 and not args.force:
        log("Ya hay un modelo activo; no se entrena nada (usa --force para reentrenar).")
        return 0

    status, body = call("POST", f"{retrain}/retrain/datasets", {"file_name": args.file}, token)
    if status == 201:
        dataset_version = body["version"]
        log(f"Dataset registrado como {dataset_version} ({body['row_count']:,} filas)")
    elif status == 409:
        _, datasets = call("GET", f"{retrain}/retrain/datasets", token=token)
        dataset_version = next(d["version"] for d in datasets if d["file_name"] == args.file)
        log(f"Dataset ya registrado como {dataset_version}")
    else:
        raise SystemExit(f"No se pudo registrar el dataset: {body}")

    status, job = call(
        "POST",
        f"{retrain}/retrain/jobs",
        {"dataset_version": dataset_version, "config_name": args.config},
        token,
    )
    if status != 202:
        raise SystemExit(f"No se pudo encolar el entrenamiento: {job}")
    log(f"Entrenamiento encolado ({args.config}); esperando al worker…")

    while job["status"] in ("queued", "running"):
        time.sleep(5)
        # El access token dura 15 min: se renueva iniciando sesión si expira.
        status, current = call("GET", f"{retrain}/retrain/jobs/{job['id']}", token=token)
        if status == 401:
            _, login = call("POST", f"{auth}/auth/login", {"email": email, "password": password})
            token = login["access_token"]
            continue
        job = current
        log(f"estado: {job['status']}")

    log(json.dumps(job, indent=2, ensure_ascii=False))
    if job["status"] != "succeeded":
        return 1
    if job["activation"] == "succeeded":
        log(f"Modelo {job['model_version']} activo. Ya puedes predecir.")
        return 0
    log("El modelo no quedó activo (rechazado por la política de calidad o activación fallida).")
    return 3


if __name__ == "__main__":
    sys.exit(main())
