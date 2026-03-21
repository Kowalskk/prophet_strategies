# Despliegue del Backend en VPS

He preparado todo para que el despliegue del motor (engine) sea lo más profesional y sencillo posible usando **Docker Compose**. Esto levantará la base de datos PostgreSQL, Redis y la API de Prophet al mismo tiempo.

## Requisitos en el VPS
1. **Docker** y **Docker Compose** instalados.
2. Git instalado para clonar el repositorio.

## Pasos para el Despliegue

1. Conéctate a tu VPS por SSH.
2. Clona tu repositorio y entra en la carpeta:
   ```bash
   git clone <URL_DE_TU_REPO>
   cd polymarket-strategies
   ```
3. (Opcional) Ajusta las variables de entorno en el archivo `docker-compose.yml` (por ejemplo, `API_SECRET`, o descomenta las variables de Polymarket para activar las órdenes reales).
4. Levanta todos los servicios en segundo plano:
   ```bash
   docker-compose up -d --build
   ```
5. ¡Listo! La API estará corriendo en el puerto `8000` de tu VPS.

## Ver los Logs
Si quieres ver qué está haciendo el motor:
```bash
docker-compose logs -f engine
```

## Para Migrar los Datos de Backtest
Si quieres copiar los mercados que probamos en simulado (SQLite) a la nueva base de datos PostgreSQL de producción, ejecuta este script dentro del contenedor:
```bash
docker-compose exec engine python -m scripts.migrate_backtest
```
