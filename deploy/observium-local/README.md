# Observium local metadata-only

Este stack levanta una instancia local de Observium Community para soporte visual y lectura de metadata.

Principios:

- sin `poller-wrapper.py` en cron
- sin discovery automático en background
- discovery solo manual y dirigido por equipo
- housekeeping habilitado
- base restaurable desde un dump SQL existente

Uso básico:

```bash
cp env.example .env
docker compose up -d
./scripts/run-discovery.sh <device_id>
```

La UI queda publicada en `http://127.0.0.1:8666`.
