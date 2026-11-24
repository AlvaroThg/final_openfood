"""Exporta la especificación OpenAPI de cada servicio y genera páginas Swagger UI.

    python scripts/export_openapi.py

Salida en docs/swagger/
    <servicio>.openapi.json   especificación OpenAPI 3
    <servicio>.html           Swagger UI con la especificación incrustada, se abre con doble clic
    index.html                enlaces a los tres servicios

No necesita base de datos ni servicios levantados: solo importa las aplicaciones y lee sus rutas.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml"), str(ROOT / "services" / "common")]

OUT = ROOT / "docs" / "swagger"
SWAGGER_CSS = "https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui.min.css"
SWAGGER_JS = "https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui-bundle.min.js"

PAGE = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="{css}">
</head>
<body>
  <div id="swagger"></div>
  <script src="{js}"></script>
  <script>
    SwaggerUIBundle({{ spec: {spec}, dom_id: "#swagger", deepLinking: false }});
  </script>
</body>
</html>
"""


def build_apps() -> dict[str, Any]:
    """Crea cada aplicación con dependencias vacías: solo hace falta que registren sus rutas."""
    from services.auth_service.app.container import Container as AuthContainer
    from services.auth_service.app.main import create_app as auth_app
    from services.auth_service.app.settings import AuthSettings
    from services.ia_backend.app.container import Container as IaContainer
    from services.ia_backend.app.main import create_app as ia_app
    from services.ia_backend.app.settings import IaSettings
    from services.retrain_service.app.container import build_container
    from services.retrain_service.app.main import create_app as retrain_app
    from services.retrain_service.app.settings import RetrainSettings

    stub: Any = MagicMock()
    auth = auth_app(
        AuthContainer.create(
            AuthSettings(database_url="postgresql://x", private_key_pem=b""),
            auth=stub,
            issuer=stub,
            health_check=lambda: None,
        )
    )
    ia = ia_app(
        IaContainer.create(
            IaSettings(database_url="postgresql://x", jwks_url="http://x/jwks"),
            registry=stub,
            store=stub,
            prediction_repo=stub,
            key_resolver=lambda _kid: None,
            health_check=lambda: None,
        )
    )
    retrain = retrain_app(
        build_container(
            RetrainSettings(database_url="postgresql://x"), pool=stub, key_resolver=lambda _k: None
        )
    )
    return {"auth_service": auth, "ia_backend": ia, "retrain_service": retrain}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    links = []
    for name, app in build_apps().items():
        spec = app.openapi()
        (OUT / f"{name}.openapi.json").write_text(
            json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        # "</" dentro de un script cerraría la etiqueta: se escapa.
        inline = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
        (OUT / f"{name}.html").write_text(
            PAGE.format(title=f"{name} API", css=SWAGGER_CSS, js=SWAGGER_JS, spec=inline),
            encoding="utf-8",
        )
        links.append(
            f'<li><a href="{name}.html">{name}</a> · <a href="{name}.openapi.json">json</a></li>'
        )
        print(f"{name}: {len(spec['paths'])} rutas")
    (OUT / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>API NutriScore</title>'
        f"<h1>API NutriScore</h1><ul>{''.join(links)}</ul>",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
