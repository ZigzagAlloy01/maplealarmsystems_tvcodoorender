# Render Cron Job para TVC

## Archivos importantes

- `tvc_sync_core.py`: logica reusable sin interfaz.
- `tvc_sync_job.py`: entrypoint automatico para Render.
- `tvc_odoo_sync.py`: interfaz manual con `tkinter`.
- `models_tvc.txt`: lista base de modelos para el job.
- `requirements.txt`: dependencias Python.

## Que ejecuta Render

Render debe correr:

```bash
python tvc_sync_job.py
```

Ese script:

- lee modelos desde `TVC_MODELS` o `TVC_MODELS_FILE`
- consulta TVC
- crea productos faltantes en Odoo
- sobrescribe productos existentes en Odoo
- actualiza compras y stock
- deja logs de resumen

## Variables de entorno en Render

Configura estas variables:

- `ODOO_PASSWORD`
- `TVC_TOKEN`

Opcionales:

- `TVC_MODELS_FILE=models_tvc.txt`
- `TVC_MODELS`

Si defines `TVC_MODELS`, tiene prioridad sobre el archivo. Puede ir separado por saltos de linea, comas o punto y coma.

## Configuracion recomendada en Render

Al crear el servicio:

- **Service Type**: `Cron Job`
- **Build Command**:

```bash
pip install -r requirements.txt
```

- **Start Command / Command**:

```bash
python tvc_sync_job.py
```

- **Schedule**:

```text
0 9 * * *
```

Eso significa una vez al dia a las 09:00 UTC. Ajusta la hora segun tu operacion. Render usa UTC.

## Flujo recomendado

1. Sube esta carpeta a un repo de GitHub.
2. Crea el Cron Job en Render.
3. Conecta el repo y la rama.
4. Configura las variables de entorno.
5. Lanza una ejecucion manual desde Render.
6. Revisa logs.
7. Si todo sale bien, deja activo el horario diario.

## Notas operativas

- El job automatico hace ambas cosas: crear nuevos y sobrescribir existentes.
- Si hay errores, `tvc_sync_job.py` termina con codigo distinto de cero para que Render marque la corrida como fallida.
- El archivo `modelos_no_encontrados_tvc.txt` se genera en cada corrida solo como apoyo de logs; no debe tratarse como almacenamiento persistente.
