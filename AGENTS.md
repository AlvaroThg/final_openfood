# AGENTS.md — OpenFood NutriScore

## 1. Propósito del documento

Este documento define los principios arquitectónicos, estándares de desarrollo, convenciones y restricciones técnicas obligatorias para OpenFood NutriScore.

Todo desarrollador o agente de IA que modifique el proyecto debe consultar y respetar estas instrucciones antes de implementar cambios.

El objetivo es construir una plataforma mantenible, segura, reproducible y preparada para evolucionar, evitando complejidad innecesaria.

### Principio rector

> Cada servicio debe tener una responsabilidad claramente delimitada. Los datos, los modelos y las operaciones deben ser trazables. Ningún cambio debe comprometer la integridad de las predicciones, la seguridad de los usuarios ni la reproducibilidad científica.

---

## 2. Arquitectura general

El proyecto utiliza una arquitectura distribuida por responsabilidades, desplegada mediante Docker Compose en Dokploy.

### Servicios

| Servicio | Responsabilidad |
|---|---|
| frontend | Interfaz de usuario y experiencia de navegación |
| auth_service | Registro, login, sesiones, JWT y JWKS |
| ia_backend | Inferencia, predicciones, historial y activación de modelos |
| retrain_service | Datasets, entrenamiento, evaluación y gestión de trabajos |
| postgres | Persistencia relacional y políticas RLS |

Los servicios se comunican mediante APIs HTTP y redes privadas de Docker.

### Reglas obligatorias

- No mezclar responsabilidades entre servicios.
- No duplicar lógica de negocio entre frontend y backend.
- No acceder directamente a PostgreSQL desde el frontend.
- No permitir que el frontend acceda directamente a los volúmenes de modelos o datasets.
- No permitir que `ia_backend` entrene modelos.
- No permitir que `retrain_service` atienda predicciones.
- No exponer servicios internos innecesariamente.
- Mantener contratos explícitos entre servicios.
- Evitar introducir nuevos servicios sin una justificación técnica.

La separación de servicios no implica que todos deban convertirse en microservicios independientes con infraestructura adicional.

---

## 3. Principios de ingeniería de software

Todo el desarrollo debe seguir estos principios:

### SOLID
- Responsabilidades únicas y cohesionadas.
- Extensión sin modificar innecesariamente código estable.
- Sustitución correcta de implementaciones.
- Interfaces pequeñas y específicas.
- Dependencia de abstracciones cuando aporten valor.

### DRY
Evitar duplicación de lógica, validaciones, reglas de negocio y transformaciones.

No crear abstracciones prematuras únicamente para eliminar unas pocas líneas repetidas.

### KISS
Preferir soluciones simples, legibles y fáciles de mantener.

### YAGNI
No implementar funcionalidades futuras sin requisitos concretos.

### Separation of Concerns
Cada módulo debe tener responsabilidades delimitadas.

### Explicit over Implicit
No utilizar comportamientos implícitos que puedan producir resultados inesperados.

### Fail Fast
Detectar errores de configuración, datos inválidos y estados inconsistentes lo antes posible.

### Dependency Inversion
Las reglas de negocio no deben depender directamente de detalles de infraestructura.

### Clean Architecture / Hexagonal
Separar, cuando aporte valor:

- Domain: entidades, reglas e invariantes.
- Application: casos de uso y orquestación.
- Infrastructure: base de datos, archivos, modelos y servicios externos.
- Presentation: endpoints, esquemas HTTP y controladores.

No forzar DDD complejo en módulos pequeños.

---

## 4. Organización del backend

Mantener responsabilidades claras dentro de cada servicio.

### ia_backend

Responsabilidades:
- Validación de solicitudes de predicción.
- Verificación de JWT.
- Autorización.
- Ejecución de inferencia.
- Registro de predicciones.
- Consulta de historial.
- Consulta y activación de versiones aprobadas.
- Exposición de información del modelo activo.

### auth_service

Responsabilidades:
- Registro de usuarios.
- Inicio y cierre de sesión.
- Hash y verificación de contraseñas.
- Emisión y renovación de tokens.
- Rotación de refresh tokens.
- Publicación de JWKS.
- Emisión de credenciales de servicio cuando corresponda.

### retrain_service

Responsabilidades:
- Recepción y validación de datasets.
- Gestión de trabajos de entrenamiento.
- Ejecución del pipeline de entrenamiento.
- Evaluación de modelos.
- Comparación con el modelo vigente.
- Registro de métricas y versiones.
- Solicitud de activación cuando se cumplan los criterios.

La API de administración y el worker de entrenamiento deben estar separados lógicamente. Los trabajos prolongados no deben depender de que una petición HTTP permanezca abierta.

### Estructura recomendada

Mantener una organización coherente por servicio:

```text
services/
├── auth_service/
│   └── app/
│       ├── api/
│       ├── application/
│       ├── domain/
│       ├── infrastructure/
│       ├── schemas/
│       └── main.py
│
├── ia_backend/
│   └── app/
│       ├── api/
│       ├── application/
│       ├── domain/
│       ├── infrastructure/
│       ├── schemas/
│       └── main.py
│
└── retrain_service/
    ├── app/
    │   ├── api/
    │   ├── application/
    │   ├── domain/
    │   ├── infrastructure/
    │   └── main.py
    └── worker/
```

No es obligatorio crear archivos o carpetas vacías. La estructura debe reflejar responsabilidades reales.

---

## 5. Principios de Machine Learning Engineering

Esta es una parte central del proyecto.

### Separación de entrenamiento e inferencia

- La inferencia debe utilizar únicamente modelos aprobados.
- El entrenamiento debe ejecutarse fuera del flujo de predicción.
- Un modelo en entrenamiento nunca debe reemplazar directamente al modelo activo.
- Una versión nueva debe superar las evaluaciones establecidas.
- Si la activación falla, debe mantenerse disponible la versión anterior.
- Las predicciones deben registrar la versión exacta del modelo utilizado.

### Versionado de modelos

Cada modelo debe tener una versión única e inmutable.

Ejemplo:

```text
models/
├── v1.0.0/
│   ├── model.pkl
│   ├── metadata.json
│   └── metrics.json
├── v1.1.0/
│   ├── model.pkl
│   ├── metadata.json
│   └── metrics.json
└── v1.2.0/
    ├── model.pkl
    ├── metadata.json
    └── metrics.json
```

No sobrescribir modelos existentes.

Cada versión debe registrar:

- Identificador.
- Fecha de entrenamiento.
- Dataset utilizado.
- Algoritmo.
- Hiperparámetros.
- Métricas.
- Versión de dependencias.
- Hash del artefacto.
- Estado de aprobación.
- Estado de activación.

### Estados del modelo

Utilizar estados explícitos y transiciones controladas.

Estados sugeridos:

- candidate
- evaluating
- approved
- active
- rejected
- archived

No permitir transiciones inválidas.

### Reproducibilidad

Todo entrenamiento debe permitir reconstruir su contexto.

Registrar:

- Semillas aleatorias cuando correspondan.
- Dataset y versión.
- Configuración del entrenamiento.
- Transformaciones aplicadas.
- Hiperparámetros.
- Dependencias.
- Métricas obtenidas.

Separar correctamente entrenamiento, validación y prueba.

No utilizar el conjunto de prueba para ajustar repetidamente el modelo.

### Calidad del modelo

Los criterios de aprobación deben estar centralizados y documentados.

Para NutriScore deben considerarse, según los requisitos científicos:

- F1 macro.
- Precision.
- Recall.
- Matriz de confusión.
- Rendimiento por clase.
- Tamaño del artefacto.
- Paridad y evaluación de sesgos cuando corresponda.

No aprobar un modelo únicamente porque supera una métrica global.

Los umbrales deben estar justificados y versionados.

---

## 6. Datos y persistencia

PostgreSQL es la fuente principal de persistencia del sistema.

### Reglas obligatorias

- Utilizar claves primarias y foráneas reales.
- Definir restricciones CHECK cuando correspondan.
- Crear índices según patrones de consulta.
- Evitar relaciones polimórficas débiles.
- Utilizar transacciones para operaciones que deban ser atómicas.
- No eliminar registros históricos sin una decisión explícita.
- Mantener consistencia entre los registros de modelos, predicciones y trabajos.
- No ejecutar consultas sin considerar el contexto de autorización.

### Migraciones

Utilizar migraciones versionadas, preferentemente Alembic.

`initdb.d` puede utilizarse para la inicialización de una base de datos vacía, pero no debe ser el mecanismo habitual para evolucionar el esquema.

Cada cambio de esquema debe incluir:
- Migración.
- Revisión de compatibilidad.
- Estrategia de recuperación cuando corresponda.
- Pruebas de integración.

No modificar manualmente producción para corregir inconsistencias que deberían resolverse mediante migraciones.

### Aislamiento de datos

Las predicciones pertenecen a un usuario.

Las consultas deben respetar ese aislamiento incluso si el frontend manipula identificadores.

Implementar RLS como defensa adicional, junto con autorización en la aplicación.

Utilizar usuarios de PostgreSQL independientes por servicio y privilegios mínimos.

Los roles de aplicación no deben ser superusuarios, propietarios de tablas ni tener BYPASSRLS.

El contexto de RLS debe configurarse dentro de la transacción correspondiente y limpiarse automáticamente al terminar.

Probar explícitamente que un usuario no pueda acceder a registros de otro.

---

## 7. Seguridad

### Autenticación

- JWT firmado mediante RS256.
- Clave privada exclusivamente en auth_service.
- Los demás servicios verifican con la clave pública.
- Validar firma, issuer, audience y expiración.
- Utilizar JWKS y una estrategia de caché.
- Access tokens de duración limitada.
- Refresh tokens opacos, almacenados mediante hash.
- Rotación de refresh tokens.
- Detectar reutilización de tokens revocados.

### Autorización

Diferenciar claramente:

- user
- admin
- service

No confiar en roles enviados por el frontend.

Validar permisos en el backend para cada operación protegida.

### Seguridad de API

- Validación estricta con Pydantic.
- Respuestas de error consistentes.
- Rate limiting.
- CORS con lista blanca.
- Protección de credenciales.
- No revelar información sensible en errores.
- No registrar tokens ni contraseñas en logs.
- Proteger endpoints internos mediante autenticación y aislamiento de red.

### Secretos

- No almacenar secretos en Git.
- Utilizar variables de entorno o mecanismos seguros de Dokploy.
- Mantener `.env.example` sin credenciales reales.
- No compartir claves privadas entre servicios innecesariamente.
- Rotar credenciales ante exposición.

### RLS

RLS es una segunda capa de defensa, no un reemplazo de autorización.

Documentar el modelo de amenazas: las variables de sesión personalizadas utilizadas por RLS deben ser configuradas exclusivamente por código confiable y no constituyen por sí mismas una protección frente a un servicio comprometido con credenciales capaces de establecer otro contexto.

---

## 8. Comunicación entre servicios

Cada servicio debe conocer únicamente los contratos que necesita.

### Reglas

- Utilizar HTTP interno para operaciones entre servicios cuando corresponda.
- No llamar a servicios mediante sus dominios públicos si existe una ruta interna.
- Definir timeouts.
- Manejar errores de conexión.
- Evitar reintentos infinitos.
- Utilizar credenciales de servicio para operaciones internas.
- No utilizar secretos compartidos genéricos como sustituto de autenticación.
- Documentar los endpoints internos.

Las operaciones de activación y recarga de modelos deben ser autenticadas, idempotentes y observables.

---

## 9. Frontend

El frontend utiliza Next.js.

Responsabilidades:

- Presentación.
- Navegación.
- Formularios.
- Validación de experiencia de usuario.
- Visualización de resultados.
- Consumo de APIs.

Reglas:

- No implementar reglas de negocio críticas exclusivamente en frontend.
- No acceder directamente a PostgreSQL.
- No almacenar refresh tokens en localStorage.
- No confiar en el middleware como mecanismo de autorización definitivo.
- Manejar correctamente estados de carga y error.
- Centralizar el cliente HTTP.
- Mantener tipos sincronizados con los contratos del backend.
- Evitar duplicar modelos de datos innecesariamente.

La interfaz debe reflejar el estado real del backend.

---

## 10. Testing

Todos los tests deben estar organizados fuera del código productivo.

Estructura:

```text
tests/
├── ml/
├── auth/
├── ia_backend/
├── retrain/
├── db/
├── integration/
├── contracts/
├── fixtures/
└── helpers/
```

### Tipos de pruebas

**Unitarias**
- Reglas de negocio.
- Validaciones.
- Preprocesamiento.
- Evaluación de modelos.
- Estados y transiciones.

**Integración**
- PostgreSQL real.
- RLS.
- Repositorios.
- Transacciones.
- Comunicación entre servicios.

**Contratos**
- Esquemas de solicitudes y respuestas.
- JWT y JWKS.
- Endpoints internos.
- Compatibilidad entre servicios.

**End-to-end**
- Registro.
- Login.
- Predicción.
- Historial.
- Administración.
- Entrenamiento.
- Activación de modelos.

### Reglas

- Cada bug importante debe producir una prueba de regresión.
- No utilizar mocks para sustituir todas las pruebas de integración.
- No considerar suficiente una prueba que solo verifica que el endpoint responde 200.
- Probar permisos y escenarios negativos.
- Probar el aislamiento entre usuarios.
- Probar fallos de infraestructura relevantes.

---

## 11. DevOps y despliegue

Utilizar:

- Docker.
- Docker Compose.
- Dokploy.
- GitHub Actions.
- PostgreSQL persistente.
- Volúmenes Docker para modelos y datasets.

### Reglas

- Cada servicio debe tener su Dockerfile.
- Utilizar imágenes reproducibles y versiones fijadas.
- Utilizar multi-stage builds cuando aporte valor.
- No depender de la etiqueta `latest` para despliegues de producción.
- No exponer PostgreSQL públicamente.
- No exponer servicios internos innecesariamente.
- Configurar healthchecks.
- Definir límites de recursos con mediciones reales.
- Separar configuración de desarrollo y producción.
- Mantener backups verificables.
- Probar procedimientos de restauración.

### CI/CD

Antes de integrar cambios importantes:

1. Lint.
2. Type checking cuando corresponda.
3. Tests unitarios.
4. Tests de integración.
5. Build de imágenes.
6. Validación de configuración Docker Compose.

No desplegar cambios que rompan los criterios de calidad establecidos.

### Migraciones en producción

Las migraciones deben tener un procedimiento controlado.

No ejecutar migraciones destructivas sin revisar compatibilidad, respaldo y recuperación.

---

## 12. Observabilidad

Implementar logs estructurados y métricas.

Registrar:

- Solicitudes recibidas.
- Errores por servicio.
- Tiempo de inferencia.
- Versión del modelo activo.
- Estado de los entrenamientos.
- Duración del entrenamiento.
- Uso de recursos.
- Fallos de comunicación.
- Errores de base de datos.

Utilizar identificadores de correlación para seguir operaciones entre servicios.

Nunca registrar contraseñas, tokens, claves privadas ni datos personales innecesarios.

---

## 13. Documentación

Mantener actualizados:

- README.md
- docs/arquitectura.md
- docs/seguridad.md
- docs/ml-lifecycle.md
- docs/model_card.md
- docs/despliegue-dokploy.md
- docs/database.md
- docs/api.md

Cuando una decisión arquitectónica relevante cambie, documentar:

- Contexto.
- Problema.
- Alternativas consideradas.
- Decisión.
- Consecuencias.

Para decisiones relevantes utilizar Architecture Decision Records (ADR).

---

## 14. Restricciones para agentes de IA

Antes de modificar código:

1. Inspeccionar la estructura existente.
2. Leer los módulos relacionados.
3. Identificar dependencias.
4. Comprender los contratos afectados.
5. Revisar los tests existentes.
6. Explicar brevemente el plan cuando el cambio sea arquitectónico o de alto impacto.

Durante la implementación:

- No reescribir módulos completos sin justificación.
- No introducir dependencias sin necesidad.
- No crear abstracciones innecesarias.
- No duplicar lógica.
- No cambiar contratos públicos sin evaluar impacto.
- No modificar políticas de seguridad sin revisar sus implicaciones.
- No eliminar datos históricos.
- No introducir cambios incompatibles silenciosamente.
- No ocultar errores mediante fallbacks silenciosos.
- No afirmar que un test pasó si no fue ejecutado.

Después de implementar:

1. Ejecutar los tests pertinentes.
2. Revisar el diff.
3. Verificar compatibilidad entre servicios.
4. Actualizar documentación.
5. Informar qué se modificó.
6. Indicar qué pruebas se ejecutaron y cuáles no.
7. Identificar riesgos pendientes.

---

## 15. Criterios de aceptación

Un cambio se considera terminado cuando:

- Cumple su requisito funcional.
- Respeta las responsabilidades de cada servicio.
- Mantiene la seguridad y autorización.
- No compromete el aislamiento de usuarios.
- Mantiene la integridad de datos.
- Incluye pruebas apropiadas.
- No rompe contratos existentes sin migración.
- Tiene manejo explícito de errores.
- No introduce complejidad innecesaria.
- Mantiene la documentación relevante actualizada.

---

## 16. Regla final

La prioridad no es producir la mayor cantidad de código ni construir la arquitectura más sofisticada.

La prioridad es mantener un sistema correcto, seguro, reproducible, comprensible y sostenible.

Cada decisión debe justificarse por una necesidad real del proyecto.

No sacrificar integridad de datos, calidad científica o seguridad por velocidad de implementación.
