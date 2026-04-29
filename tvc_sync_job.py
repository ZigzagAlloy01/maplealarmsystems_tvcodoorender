import os
import sys
import traceback

from tvc_sync_core import DEFAULT_MODELS, TVCSyncCore


def cargar_modelos():
    modelos_env = os.getenv("TVC_MODELS", "").strip()
    if modelos_env:
        texto = modelos_env.replace(",", "\n").replace(";", "\n")
        return TVCSyncCore.parsear_modelos_texto(texto)

    modelos_file = os.getenv("TVC_MODELS_FILE", "models_tvc.txt").strip() or "models_tvc.txt"
    if os.path.exists(modelos_file):
        with open(modelos_file, "r", encoding="utf-8") as f:
            return TVCSyncCore.parsear_modelos_texto(f.read())

    return TVCSyncCore.parsear_modelos_texto(DEFAULT_MODELS)


def main():
    try:
        modelos = cargar_modelos()
        if not modelos:
            raise RuntimeError("No hay modelos para sincronizar")

        sync = TVCSyncCore(logger=print)
        print(f"Modelos a sincronizar: {len(modelos)}")

        resultado = sync.run_daily_sync(modelos)
        sync.guardar_modelos_no_encontrados("modelos_no_encontrados_tvc.txt")

        carga = resultado["carga"]
        preview = resultado["preview"]
        resumen = resultado["sync"]

        print("")
        print("Resumen de carga:")
        print(f"- Solicitados: {carga['modelos_solicitados']}")
        print(f"- Encontrados en TVC: {carga['encontrados']}")
        print(f"- No encontrados en TVC: {carga['no_encontrados']}")
        print(f"- Tipo de cambio aplicado: {carga['tipo_cambio'] if carga['tipo_cambio'] else 'no disponible'}")

        print("")
        print("Resumen de preview:")
        print(f"- Nuevos detectados: {preview['nuevos']}")
        print(f"- Coincidencias en Odoo: {preview['coincidencias']}")
        print(f"- Saltados: {preview['saltados']}")

        print("")
        print("Resumen de sincronizacion:")
        print(f"- Creados: {resumen['creados']}")
        print(f"- Sobrescritos: {resumen['sobrescritos']}")
        print(f"- Omitidos: {resumen['omitidos']}")
        print(f"- Errores: {resumen['errores']}")

        return 1 if resumen["errores"] > 0 else 0
    except Exception as e:
        print(f"Fallo la ejecucion automatica TVC: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
