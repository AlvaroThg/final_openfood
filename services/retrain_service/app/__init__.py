"""retrain_service: datasets, trabajos de entrenamiento, evaluación y solicitud de activación.

Dos procesos con la misma imagen y el mismo código:
    app/     API HTTP de administración (solo admin): registra datasets y encola trabajos.
             Responde de inmediato; nunca entrena dentro de una petición.
    worker/  bucle que toma trabajos de la tabla retrain_jobs (FOR UPDATE SKIP LOCKED),
             entrena con el paquete nutriscore, aplica la política de calidad y, si aprueba,
             PIDE a ia_backend que active la versión. Nunca atiende predicciones.

Los imports internos son relativos: el paquete funciona como `retrain_service` en el contenedor
y como `services.retrain_service` en las pruebas.
"""
