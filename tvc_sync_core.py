import base64
import os
import re
import xmlrpc.client
from urllib.parse import urljoin

import requests

try:
    from config import MAPLE_PASSWORD
except ImportError:
    MAPLE_PASSWORD = os.getenv("ODOO_PASSWORD", "")

try:
    from config import TVC_TOKEN
except ImportError:
    TVC_TOKEN = os.getenv("TVC_TOKEN") or os.getenv("TOKEN", "")

ODOO_URL = "https://maplealarmsystems.odoo.com"
ODOO_DB = "maplealarmsystems"
ODOO_USERNAME = "sistemas@storemaple.com"
ODOO_PASSWORD = MAPLE_PASSWORD

TVC_API_BASE_URL = "https://api.tvc.mx"
TVC_MEDIA_BASE_URL = "https://api.tvc.mx"
REQUEST_TIMEOUT = 30

PRICE_SOURCE_KEY = "distributor_price"
PRICE_LIST_KEY = "list_price"
DISPLAY_CURRENCY = "MXN"
DISPLAY_PRICES_WITHOUT_TAX = True
SOURCE_PRICE_CURRENCY = "USD"

AUTO_CONVERT_USD_TO_MXN = True

CLEAR_QUOTATION_DESCRIPTION = True
SUPPLIER_NAME = "TVC En Linea"
SUPPLIER_UOM_NAME = "Unidades"
SUPPLIER_CURRENCY_NAME = "MXN"
SUPPLIER_MIN_QTY = 1.0
SALE_FACTOR_1 = 1.04
SALE_FACTOR_IVA = 1.14

DEFAULT_MODELS = """HS2LCDWFDMK
HS2ICN
HS2ICNP
IQKP-915
HS2LCD N
HS2LCD RED
HS2ICNRF9
HS2LCDP N
HS2LCDRF9 N
HS2LCDPRO
HS2LCDRFPRO9
HS2LCDRFP9 N
HS2LCDWFP9 N
HS2TCHPBLK N
HS2TCHP N
HS2TCHPRO
"""


class TVCSyncCore:
    def __init__(self, logger=None):
        self.logger = logger or print
        self.validar_configuracion()

        self.session = requests.Session()
        self.session.headers.update(self.obtener_headers_tvc())

        self.common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        self.uid = self.common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
        if not self.uid:
            raise Exception("Error de autenticacion con Odoo. Verifica credenciales/API Key")

        version = self.common.version()
        self.log(f"Conectado a Odoo: {version}")

        self.models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        self.product_template_fields = self.obtener_campos_modelo("product.template")
        self.unspsc_fields = self.obtener_campos_modelo("product.unspsc.code")
        self.supplierinfo_fields = self.obtener_campos_modelo("product.supplierinfo")
        self.partner_fields = self.obtener_campos_modelo("res.partner")
        self.uom_fields = self.obtener_campos_modelo("uom.uom")
        self.currency_fields = self.obtener_campos_modelo("res.currency")

        self.productos_cache = {}
        self.preview_cache = []
        self.tax_cache = {}
        self.unspsc_cache = {}
        self.modelos_no_encontrados = []
        self.exchange_rate_cache = None
        self.last_exchange_rate = None
        self.partner_cache = {}
        self.uom_cache = {}
        self.currency_cache = {}

    def log(self, mensaje):
        if self.logger:
            self.logger(str(mensaje))

    def validar_configuracion(self):
        faltantes = []
        if not ODOO_PASSWORD:
            faltantes.append("MAPLE_PASSWORD / ODOO_PASSWORD")
        if not TVC_TOKEN:
            faltantes.append("TVC_TOKEN o TOKEN")

        if faltantes:
            raise Exception(
                "Faltan variables de configuracion: "
                + ", ".join(faltantes)
                + ". Define estos valores en config.py o variables de entorno."
            )

    def obtener_campos_modelo(self, modelo):
        try:
            data = self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                modelo,
                "fields_get",
                [],
                {"attributes": ["string", "type"]},
            )
            return set(data.keys())
        except Exception as e:
            self.log(f"No fue posible consultar fields_get para {modelo}: {e}")
            return set()

    def obtener_headers_tvc(self):
        return {
            "Authorization": f"Bearer {TVC_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def obtener_exchange_rate(self):
        if self.exchange_rate_cache is not None:
            return self.exchange_rate_cache

        try:
            response = self.session.get(f"{TVC_API_BASE_URL}/exchange-rates", timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self.exchange_rate_cache = float(response.json())
        except Exception as e:
            self.log(f"No se pudo obtener tipo de cambio comercial TVC: {e}")
            self.exchange_rate_cache = None

        return self.exchange_rate_cache

    def refrescar_exchange_rate(self):
        self.exchange_rate_cache = None
        self.last_exchange_rate = self.obtener_exchange_rate()
        return self.last_exchange_rate

    @staticmethod
    def parsear_modelos_texto(texto):
        vistos = set()
        modelos = []
        for linea in str(texto or "").splitlines():
            modelo = linea.strip()
            if not modelo or modelo.lower().startswith("pega aqui"):
                continue
            modelo_upper = modelo.upper()
            if modelo_upper not in vistos:
                modelos.append(modelo)
                vistos.add(modelo_upper)
        return modelos

    def chunked(self, items, size):
        for idx in range(0, len(items), size):
            yield items[idx : idx + size]

    def normalizar_numero(self, valor):
        if valor is None:
            return None

        if isinstance(valor, (int, float)):
            return float(valor)

        texto = str(valor).strip()
        if not texto:
            return None

        match = re.search(r"-?\d[\d.,]*", texto)
        if not match:
            return None

        numero = match.group(0)
        if "," in numero and "." not in numero:
            numero = numero.replace(",", ".")
        else:
            numero = numero.replace(",", "")

        try:
            return float(numero)
        except ValueError:
            return None

    def buscar_clave_recursiva(self, data, claves):
        claves_normalizadas = {str(clave).strip().lower() for clave in claves}

        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).strip().lower() in claves_normalizadas:
                    return value
            for value in data.values():
                encontrado = self.buscar_clave_recursiva(value, claves)
                if encontrado is not None:
                    return encontrado

        if isinstance(data, list):
            for item in data:
                encontrado = self.buscar_clave_recursiva(item, claves)
                if encontrado is not None:
                    return encontrado

        return None

    def obtener_modelo_producto(self, producto):
        return str(producto.get("tvc_model") or "").strip()

    def obtener_referencia_producto(self, producto):
        return str(producto.get("provider_model") or "").strip()

    def obtener_nombre_producto(self, producto):
        return str(producto.get("name") or "Producto TVC").strip()

    def obtener_clave_referencia_odoo(self, producto):
        referencia = self.obtener_referencia_producto(producto)
        if referencia:
            return referencia
        return self.obtener_modelo_producto(producto)

    def obtener_sat_producto(self, producto):
        valor = self.buscar_clave_recursiva(producto, ("sat_key", "sat"))
        return str(valor or "").strip()

    def obtener_stock_producto(self, producto):
        total = self.normalizar_numero(producto.get("total_inventories"))
        if total is not None:
            return total

        detailed = producto.get("inventory_detailed") or []
        acumulado = 0.0
        hubo_valores = False
        for item in detailed:
            cantidad = self.normalizar_numero((item or {}).get("quantity"))
            if cantidad is not None:
                acumulado += cantidad
                hubo_valores = True
        return acumulado if hubo_valores else 0.0

    def obtener_peso_producto(self, producto):
        valor = self.buscar_clave_recursiva(
            producto,
            ("piece_weight", "weight", "peso", "peso_kg", "peso_neto", "peso_bruto"),
        )
        return self.normalizar_numero(valor)

    def obtener_volumen_producto(self, producto):
        valor_directo = self.buscar_clave_recursiva(
            producto,
            ("volume", "volumen", "volumen_m3", "cubicaje"),
        )
        volumen_directo = self.normalizar_numero(valor_directo)
        if volumen_directo is not None:
            return volumen_directo

        dimensiones = producto.get("weights_and_dimensions") or {}
        alto = self.normalizar_numero(dimensiones.get("piece_height"))
        largo = self.normalizar_numero(dimensiones.get("piece_length"))
        ancho = self.normalizar_numero(dimensiones.get("piece_width"))

        if alto is None or largo is None or ancho is None:
            return None

        volumen_cm3 = alto * largo * ancho
        volumen_m3 = volumen_cm3 / 1_000_000.0
        return round(volumen_m3, 6)

    def obtener_precio_lista(self, producto):
        precio = self.normalizar_numero(producto.get(PRICE_LIST_KEY)) or 0.0
        return self.convertir_precio_a_mxn(precio)

    def obtener_precio_descuento_base(self, producto):
        return self.normalizar_numero(producto.get(PRICE_SOURCE_KEY)) or 0.0

    def convertir_precio_a_mxn(self, precio):
        if not AUTO_CONVERT_USD_TO_MXN:
            return round(precio, 2)

        tipo_cambio = self.obtener_exchange_rate()
        if not tipo_cambio:
            return round(precio, 2)

        return round(precio * tipo_cambio, 2)

    def obtener_precio_descuento(self, producto):
        precio = self.obtener_precio_descuento_base(producto)
        return self.convertir_precio_a_mxn(precio)

    def obtener_multiplicador_venta(self, precio_compra):
        if precio_compra <= 0:
            return 0.0
        if precio_compra < 50:
            return 3
        if precio_compra < 100:
            return 2
        if precio_compra < 1000:
            return 1.8
        if precio_compra < 1500:
            return 1.7
        if precio_compra < 2000:
            return 1.6
        if precio_compra < 3000:
            return 1.5
        if precio_compra < 10000:
            return 1.4
        if precio_compra < 20000:
            return 1.3
        return 1.2

    def obtener_precio_venta(self, producto):
        precio_compra = self.obtener_precio_descuento(producto)
        if precio_compra <= 0:
            return 0.0

        multiplicador = self.obtener_multiplicador_venta(precio_compra)
        precio_venta = precio_compra * SALE_FACTOR_1 * multiplicador * SALE_FACTOR_IVA
        return round(precio_venta, 2)

    def obtener_descuentos_aplicables(self, producto):
        descuentos = producto.get("applicable_discounts")
        if isinstance(descuentos, dict):
            partes = []
            for nombre, valor in descuentos.items():
                partes.append(f"{nombre}: {valor}")
            return ", ".join(partes) if partes else "N/A"

        if isinstance(descuentos, list):
            return ", ".join(str(item) for item in descuentos) if descuentos else "N/A"

        return str(descuentos or "N/A")

    def obtener_descuentos_por_volumen(self, producto):
        opciones = producto.get("volume_product_discount_options") or []
        partes = []
        for opcion in opciones:
            cantidad = opcion.get("quantity")
            moneda = opcion.get("currency") or ""
            precio = self.normalizar_numero(opcion.get("price"))
            if cantidad is None or precio is None:
                continue
            if AUTO_CONVERT_USD_TO_MXN and str(moneda).upper() == SOURCE_PRICE_CURRENCY:
                precio = self.convertir_precio_a_mxn(precio)
                moneda = DISPLAY_CURRENCY
            partes.append(f"{cantidad}+ -> {precio:.2f} {moneda}".strip())
        return " | ".join(partes) if partes else "N/A"

    def obtener_url_imagen(self, producto):
        media = producto.get("media") or {}
        candidatos = [
            media.get("main_image"),
            *(media.get("gallery") or []),
        ]

        for candidato in candidatos:
            if not isinstance(candidato, str) or not candidato.strip():
                continue
            if candidato.startswith("http://") or candidato.startswith("https://"):
                return candidato
            return urljoin(TVC_MEDIA_BASE_URL, candidato)
        return None

    def obtener_imagen_odoo(self, producto):
        url_imagen = self.obtener_url_imagen(producto)
        if not url_imagen:
            return None

        try:
            response = self.session.get(url_imagen, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return base64.b64encode(response.content).decode("ascii")
        except Exception as e:
            self.log(f"No se pudo descargar imagen desde {url_imagen}: {e}")
            return None

    def buscar_unspsc_odoo(self, sat_key):
        sat_key = (sat_key or "").strip()
        if not sat_key:
            return None

        if sat_key in self.unspsc_cache:
            return self.unspsc_cache[sat_key]

        candidate_fields = [field for field in ("code", "name", "unspsc_code") if field in self.unspsc_fields]
        if not candidate_fields:
            self.unspsc_cache[sat_key] = None
            return None

        base_domain = []
        if "applies_to" in self.unspsc_fields:
            base_domain.append(["applies_to", "=", "product"])

        for field in candidate_fields:
            try:
                encontrados = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "product.unspsc.code",
                    "search_read",
                    [[*base_domain, [field, "=", sat_key]]],
                    {"fields": ["id", field], "limit": 1},
                )
                if encontrados:
                    self.unspsc_cache[sat_key] = encontrados[0]["id"]
                    return encontrados[0]["id"]
            except Exception as e:
                self.log(f"No se pudo buscar UNSPSC por {field} para SAT {sat_key}: {e}")

        if "name" in self.unspsc_fields:
            try:
                encontrados = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "product.unspsc.code",
                    "search_read",
                    [[*base_domain, ["name", "ilike", sat_key]]],
                    {"fields": ["id", "name"], "limit": 1},
                )
                if encontrados:
                    self.unspsc_cache[sat_key] = encontrados[0]["id"]
                    return encontrados[0]["id"]
            except Exception as e:
                self.log(f"No se pudo hacer fallback UNSPSC para SAT {sat_key}: {e}")

        self.unspsc_cache[sat_key] = None
        return None

    def fetch_productos_tvc_por_modelos(self, modelos):
        encontrados = {}

        for lote in self.chunked(modelos, 50):
            params = []
            for modelo in lote:
                params.append(("tvcModels[]", modelo))

            params.extend(
                [
                    ("withInventory", "simple"),
                    ("withPrice", "true"),
                    ("withMedia", "true"),
                    ("withWeightsAndDimensions", "true"),
                    ("withCategoryBreadcrumb", "true"),
                    ("perPage", str(max(50, len(lote)))),
                ]
            )

            response = self.session.get(
                f"{TVC_API_BASE_URL}/products",
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()

            for producto in payload.get("data", []):
                modelo = self.obtener_modelo_producto(producto).upper()
                if modelo:
                    encontrados[modelo] = producto

        return encontrados

    def cargar_productos(self, modelos):
        if not modelos:
            raise ValueError("No hay modelos para consultar")

        self.productos_cache.clear()
        self.preview_cache = []
        self.modelos_no_encontrados = []

        tipo_cambio = self.refrescar_exchange_rate() if AUTO_CONVERT_USD_TO_MXN else None
        productos_por_modelo = self.fetch_productos_tvc_por_modelos(modelos)

        encontrados = 0
        for modelo in modelos:
            producto = productos_por_modelo.get(modelo.strip().upper())
            if not producto:
                self.modelos_no_encontrados.append(modelo)
                continue

            tvc_id = str(producto.get("tvc_id", ""))
            self.productos_cache[tvc_id] = producto
            encontrados += 1

        return {
            "modelos_solicitados": len(modelos),
            "encontrados": encontrados,
            "no_encontrados": len(self.modelos_no_encontrados),
            "tipo_cambio": tipo_cambio,
            "moneda_mostrada": DISPLAY_CURRENCY,
            "moneda_origen": SOURCE_PRICE_CURRENCY,
        }

    def guardar_modelos_no_encontrados(self, path):
        with open(path, "w", encoding="utf-8") as f:
            for modelo in self.modelos_no_encontrados:
                f.write(modelo + "\n")

    def formatear_estado_carga(self, summary):
        tipo_cambio = summary.get("tipo_cambio")
        return (
            f"Lista procesada.\n\n"
            f"Modelos solicitados: {summary['modelos_solicitados']}\n"
            f"Encontrados en TVC: {summary['encontrados']}\n"
            f"No encontrados: {summary['no_encontrados']}\n"
            f"Moneda mostrada: {DISPLAY_CURRENCY}\n"
            f"Moneda origen TVC: {SOURCE_PRICE_CURRENCY}\n"
            f"Tipo de cambio aplicado: {f'{tipo_cambio:.4f}' if tipo_cambio else 'no disponible'}\n"
            f"IVA considerado en precio mostrado: {'no' if DISPLAY_PRICES_WITHOUT_TAX else 'si'}\n\n"
            "Haz clic en 'Previsualizar Odoo' para revisar estado en Odoo."
        )

    def es_fault_none_xmlrpc(self, error):
        return isinstance(error, xmlrpc.client.Fault) and "cannot marshal None unless allow_none is enabled" in error.faultString

    def obtener_impuestos_por_ids(self, tax_ids):
        faltantes = [tax_id for tax_id in tax_ids if tax_id not in self.tax_cache]
        if faltantes:
            try:
                taxes = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "account.tax",
                    "read",
                    [faltantes],
                    {"fields": ["name", "amount", "type_tax_use", "price_include"]},
                )
                for tax in taxes:
                    self.tax_cache[tax["id"]] = tax
            except Exception as e:
                self.log(f"No se pudieron consultar impuestos: {e}")

        return [self.tax_cache[tax_id] for tax_id in tax_ids if tax_id in self.tax_cache]

    def formatear_impuestos(self, tax_ids):
        taxes = self.obtener_impuestos_por_ids(tax_ids)
        if not taxes:
            return "sin impuestos configurados"

        partes = []
        for tax in taxes:
            sufijo = " incluido" if tax.get("price_include") else ""
            partes.append(f"{tax.get('name', 'Impuesto')} ({tax.get('amount', 0)}%{sufijo})")
        return ", ".join(partes)

    def buscar_producto_odoo(self, referencia, modelo_tvc=None):
        fields = ["id", "name", "default_code", "list_price", "taxes_id"]
        if "unspsc_code_id" in self.product_template_fields:
            fields.append("unspsc_code_id")
        if "weight" in self.product_template_fields:
            fields.append("weight")
        if "volume" in self.product_template_fields:
            fields.append("volume")
        if "is_storable" in self.product_template_fields:
            fields.append("is_storable")
        if "image_1920" in self.product_template_fields:
            fields.append("image_1920")

        candidatos = []
        if referencia:
            candidatos.append(referencia)
        if modelo_tvc and modelo_tvc not in candidatos:
            candidatos.append(modelo_tvc)

        for clave in candidatos:
            encontrados = self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                "product.template",
                "search_read",
                [[["default_code", "=", clave]]],
                {"fields": fields, "limit": 1},
            )
            if encontrados:
                return encontrados

        return []

    def buscar_partner_id(self, nombre_partner):
        cache_key = nombre_partner.strip().lower()
        if cache_key in self.partner_cache:
            return self.partner_cache[cache_key]

        dominios = []
        if "name" in self.partner_fields:
            dominios.append([["name", "=", nombre_partner]])
            dominios.append([["name", "ilike", nombre_partner]])

        for domain in dominios:
            try:
                partners = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "res.partner",
                    "search_read",
                    [domain],
                    {"fields": ["id", "name"], "limit": 1},
                )
                if partners:
                    partner_id = partners[0]["id"]
                    self.partner_cache[cache_key] = partner_id
                    return partner_id
            except Exception as e:
                self.log(f"No se pudo buscar proveedor '{nombre_partner}': {e}")

        self.partner_cache[cache_key] = None
        return None

    def buscar_uom_id(self, nombre_uom):
        cache_key = nombre_uom.strip().lower()
        if cache_key in self.uom_cache:
            return self.uom_cache[cache_key]

        dominios = []
        if "name" in self.uom_fields:
            dominios.append([["name", "=", nombre_uom]])
            dominios.append([["name", "ilike", nombre_uom]])
            dominios.append([["name", "ilike", "Unidad"]])
            dominios.append([["name", "ilike", "Unid"]])
            dominios.append([["name", "ilike", "Unit"]])

        for domain in dominios:
            try:
                uoms = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "uom.uom",
                    "search_read",
                    [domain],
                    {"fields": ["id", "name"], "limit": 1},
                )
                if uoms:
                    uom_id = uoms[0]["id"]
                    self.uom_cache[cache_key] = uom_id
                    return uom_id
            except Exception as e:
                self.log(f"No se pudo buscar unidad '{nombre_uom}': {e}")

        self.uom_cache[cache_key] = None
        return None

    def buscar_currency_id(self, currency_name):
        cache_key = currency_name.strip().lower()
        if cache_key in self.currency_cache:
            return self.currency_cache[cache_key]

        dominios = []
        if "name" in self.currency_fields:
            dominios.append([["name", "=", currency_name]])
            dominios.append([["name", "ilike", currency_name]])
        if "full_name" in self.currency_fields:
            dominios.append([["full_name", "ilike", currency_name]])
        if "symbol" in self.currency_fields:
            dominios.append([["symbol", "=", currency_name]])

        for domain in dominios:
            try:
                currencies = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "res.currency",
                    "search_read",
                    [domain],
                    {"fields": ["id", "name", "symbol"], "limit": 1},
                )
                if currencies:
                    currency_id = currencies[0]["id"]
                    self.currency_cache[cache_key] = currency_id
                    return currency_id
            except Exception as e:
                self.log(f"No se pudo buscar moneda '{currency_name}': {e}")

        self.currency_cache[cache_key] = None
        return None

    def obtener_uom_template_id(self, template_id):
        fields = []
        if "uom_po_id" in self.product_template_fields:
            fields.append("uom_po_id")
        if "uom_id" in self.product_template_fields:
            fields.append("uom_id")
        if not fields:
            return None

        try:
            templates = self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                "product.template",
                "read",
                [[template_id]],
                {"fields": fields},
            )
            if not templates:
                return None

            template = templates[0]
            for field in ("uom_po_id", "uom_id"):
                valor = template.get(field)
                if isinstance(valor, list) and valor:
                    return valor[0]
            return None
        except Exception as e:
            self.log(f"No se pudo leer la unidad del producto template {template_id}: {e}")
            return None

    def actualizar_proveedor_compra(self, template_id, producto):
        partner_id = self.buscar_partner_id(SUPPLIER_NAME)
        if not partner_id:
            self.log(f"No se encontro el proveedor '{SUPPLIER_NAME}' en Odoo")
            return

        uom_id = self.buscar_uom_id(SUPPLIER_UOM_NAME)
        currency_id = self.buscar_currency_id(SUPPLIER_CURRENCY_NAME)
        if not currency_id:
            self.log(f"No se encontro la moneda '{SUPPLIER_CURRENCY_NAME}' en Odoo")

        if "partner_id" not in self.supplierinfo_fields:
            self.log("El modelo product.supplierinfo no expone el campo partner_id en esta base")
            return

        domain = [["partner_id", "=", partner_id]]
        if "product_tmpl_id" in self.supplierinfo_fields:
            domain.append(["product_tmpl_id", "=", template_id])

        supplier_fields = ["id", "partner_id"]
        if "product_tmpl_id" in self.supplierinfo_fields:
            supplier_fields.append("product_tmpl_id")
        if "price" in self.supplierinfo_fields:
            supplier_fields.append("price")
        if "product_uom_id" in self.supplierinfo_fields:
            supplier_fields.append("product_uom_id")
        if "currency_id" in self.supplierinfo_fields:
            supplier_fields.append("currency_id")

        supplier_lines = self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            "product.supplierinfo",
            "search_read",
            [domain],
            {"fields": supplier_fields, "limit": 1},
        )

        if not uom_id and supplier_lines:
            existente_uom = supplier_lines[0].get("product_uom_id")
            if isinstance(existente_uom, list) and existente_uom:
                uom_id = existente_uom[0]

        if not uom_id:
            uom_id = self.obtener_uom_template_id(template_id)

        if not uom_id:
            self.log(
                f"No se encontro la unidad '{SUPPLIER_UOM_NAME}' en Odoo ni una unidad fallback en el producto {template_id}; "
                "se actualizara proveedor sin tocar unidad."
            )

        vals = {"partner_id": partner_id}
        if "min_qty" in self.supplierinfo_fields:
            vals["min_qty"] = SUPPLIER_MIN_QTY
        if "product_uom_id" in self.supplierinfo_fields and uom_id:
            vals["product_uom_id"] = uom_id
        if "price" in self.supplierinfo_fields:
            vals["price"] = self.obtener_precio_descuento(producto)
        if "currency_id" in self.supplierinfo_fields and currency_id:
            vals["currency_id"] = currency_id

        if supplier_lines:
            supplierinfo_id = supplier_lines[0]["id"]
            self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                "product.supplierinfo",
                "write",
                [[supplierinfo_id], vals],
            )
        else:
            if "product_tmpl_id" in self.supplierinfo_fields:
                vals["product_tmpl_id"] = template_id
            self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                "product.supplierinfo",
                "create",
                [vals],
            )

    def construir_data_odoo(self, producto):
        nombre = self.obtener_nombre_producto(producto)
        modelo = self.obtener_modelo_producto(producto)
        referencia = self.obtener_clave_referencia_odoo(producto)
        precio_venta = self.obtener_precio_venta(producto)
        sat_key = self.obtener_sat_producto(producto)
        peso = self.obtener_peso_producto(producto)
        volumen = self.obtener_volumen_producto(producto)

        data = {
            "name": nombre,
            "default_code": referencia,
            "list_price": precio_venta,
            "sale_ok": True,
        }

        if "description_sale" in self.product_template_fields:
            data["description_sale"] = "" if CLEAR_QUOTATION_DESCRIPTION else nombre

        if "unspsc_code_id" in self.product_template_fields and sat_key:
            unspsc_id = self.buscar_unspsc_odoo(sat_key)
            if unspsc_id:
                data["unspsc_code_id"] = unspsc_id
            else:
                self.log(f"No se encontro codigo UNSPSC en Odoo para SAT {sat_key} del modelo {modelo}")

        if "weight" in self.product_template_fields and peso is not None:
            data["weight"] = peso

        if "volume" in self.product_template_fields and volumen is not None:
            data["volume"] = volumen

        if "is_storable" in self.product_template_fields:
            data["is_storable"] = True
        elif "detailed_type" in self.product_template_fields:
            data["detailed_type"] = "product"
        elif "type" in self.product_template_fields:
            data["type"] = "product"

        if "image_1920" in self.product_template_fields:
            image_b64 = self.obtener_imagen_odoo(producto)
            if image_b64:
                data["image_1920"] = image_b64

        return data

    def obtener_product_id_desde_template(self, template_id):
        variantes = self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            "product.product",
            "search_read",
            [[["product_tmpl_id", "=", template_id]]],
            {"fields": ["id"], "limit": 1},
        )
        if not variantes:
            return None
        return variantes[0]["id"]

    def obtener_ubicacion_interna(self):
        ubicaciones = self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            "stock.location",
            "search_read",
            [[["usage", "=", "internal"]]],
            {"fields": ["id", "name"], "limit": 1},
        )
        if not ubicaciones:
            return None
        return ubicaciones[0]

    def actualizar_stock(self, template_id, stock):
        try:
            product_id = self.obtener_product_id_desde_template(template_id)
            if not product_id:
                self.log(f"No se encontro variante para template {template_id}")
                return

            ubicacion = self.obtener_ubicacion_interna()
            if not ubicacion:
                self.log("No se encontro ubicacion interna de inventario")
                return

            location_id = ubicacion["id"]
            location_name = ubicacion["name"]

            quants = self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                "stock.quant",
                "search",
                [[["product_id", "=", product_id], ["location_id", "=", location_id]]],
                {"limit": 1},
            )

            if quants:
                quant_id = quants[0]
                self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "stock.quant",
                    "write",
                    [[quant_id], {"inventory_quantity": stock}],
                )
            else:
                quant_id = self.models.execute_kw(
                    ODOO_DB,
                    self.uid,
                    ODOO_PASSWORD,
                    "stock.quant",
                    "create",
                    [{"product_id": product_id, "location_id": location_id, "inventory_quantity": stock}],
                )

            self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                "stock.quant",
                "action_apply_inventory",
                [[quant_id]],
            )
            self.log(f"Stock ajustado a {stock:.0f} en {location_name}")
        except Exception as e:
            if self.es_fault_none_xmlrpc(e):
                self.log(
                    "Stock ajustado correctamente, pero Odoo devolvio un None por XML-RPC "
                    "despues de aplicar inventario. Se toma como exitoso."
                )
                return
            self.log(f"Error actualizando stock: {e}")

    def construir_preview(self):
        preview = []
        for producto in self.productos_cache.values():
            nombre = self.obtener_nombre_producto(producto)
            modelo = self.obtener_modelo_producto(producto)
            referencia = self.obtener_referencia_producto(producto) or "N/A"
            clave_odoo = self.obtener_clave_referencia_odoo(producto)
            precio_compra = self.obtener_precio_descuento(producto)
            precio_venta = self.obtener_precio_venta(producto)
            stock = self.obtener_stock_producto(producto)
            volumen = self.obtener_volumen_producto(producto)
            sat_key = self.obtener_sat_producto(producto)
            tiene_imagen = bool(self.obtener_url_imagen(producto))

            if not modelo:
                preview.append(
                    {
                        "accion": "saltado",
                        "motivo": "sin modelo",
                        "modelo": "",
                        "nombre": nombre,
                        "producto": producto,
                    }
                )
                continue

            existentes = self.buscar_producto_odoo(clave_odoo, modelo)
            if existentes:
                existente = existentes[0]
                preview.append(
                    {
                        "accion": "coincidencia",
                        "modelo": modelo,
                        "referencia": referencia,
                        "clave_odoo": clave_odoo,
                        "nombre": nombre,
                        "precio_compra_tvc": precio_compra,
                        "precio_venta_tvc": precio_venta,
                        "stock_tvc": stock,
                        "volumen_tvc": volumen,
                        "sat_key": sat_key,
                        "tiene_imagen": tiene_imagen,
                        "odoo_id": existente["id"],
                        "odoo_nombre": existente["name"],
                        "odoo_precio": existente.get("list_price", 0),
                        "odoo_impuestos": self.formatear_impuestos(existente.get("taxes_id", [])),
                        "producto": producto,
                    }
                )
            else:
                preview.append(
                    {
                        "accion": "crear",
                        "modelo": modelo,
                        "referencia": referencia,
                        "clave_odoo": clave_odoo,
                        "nombre": nombre,
                        "precio_compra_tvc": precio_compra,
                        "precio_venta_tvc": precio_venta,
                        "stock_tvc": stock,
                        "volumen_tvc": volumen,
                        "sat_key": sat_key,
                        "tiene_imagen": tiene_imagen,
                        "producto": producto,
                    }
                )
        return preview

    def separar_preview_acciones(self):
        crear = [p for p in self.preview_cache if p["accion"] == "crear"]
        coincidencias = [p for p in self.preview_cache if p["accion"] == "coincidencia"]
        saltados = [p for p in self.preview_cache if p["accion"] == "saltado"]
        return crear, coincidencias, saltados

    def formatear_preview(self):
        crear, coincidencias, saltados = self.separar_preview_acciones()

        lineas = [
            "PREVIEW DE LISTA DE MODELOS TVC",
            "",
            f"Productos encontrados en TVC: {len(self.preview_cache)}",
            f"Nuevos para crear: {len(crear)}",
            f"Coincidencias en Odoo: {len(coincidencias)}",
            f"Saltados: {len(saltados)}",
            f"Modelos no encontrados en TVC: {len(self.modelos_no_encontrados)}",
            f"Moneda mostrada: {DISPLAY_CURRENCY}",
            f"Moneda origen TVC: {SOURCE_PRICE_CURRENCY}",
            f"Precio base usado: {PRICE_SOURCE_KEY}",
            f"Tipo de cambio aplicado: {f'{self.last_exchange_rate:.4f}' if self.last_exchange_rate else 'no disponible'}",
            f"IVA agregado por el script: {'si' if not DISPLAY_PRICES_WITHOUT_TAX else 'no'}",
            "",
            "Campos que se enviaran a Odoo en cada alta/actualizacion:",
            "- TVC name -> name",
            "- TVC referencia -> default_code",
            "- TVC distributor_price -> price de compras",
            "- Venta list_price -> precio de compra por tabla de rangos",
            "- TVC sat_key -> unspsc_code_id",
            "- TVC piece_weight -> weight",
            "- TVC piece dimensions -> volume (m3)",
            "- TVC media.main_image -> image_1920",
            "- TVC total_inventories -> stock.quant",
            f"- Compras proveedor -> {SUPPLIER_NAME}",
            f"- Compras cantidad minima -> {SUPPLIER_MIN_QTY:.0f}",
            f"- Compras unidad -> {SUPPLIER_UOM_NAME}",
            "- Compras precio unitario -> TVC distributor_price",
            "",
        ]

        if self.modelos_no_encontrados:
            lineas.append("MODELOS NO ENCONTRADOS:")
            for modelo in self.modelos_no_encontrados[:80]:
                lineas.append(f"- {modelo}")
            if len(self.modelos_no_encontrados) > 80:
                lineas.append(f"... y {len(self.modelos_no_encontrados) - 80} modelos mas")
            lineas.append("")

        if coincidencias:
            lineas.append("EXISTEN EN ODOO:")
            for item in coincidencias[:50]:
                volumen_txt = f"{item['volumen_tvc']:.6f}" if item["volumen_tvc"] is not None else "N/A"
                lineas.append(
                    f"- {item['modelo']} | ref {item['referencia']} | ODOO {item['odoo_precio']:.2f} | "
                    f"Compra {item['precio_compra_tvc']:.2f} | Venta {item['precio_venta_tvc']:.2f} | stock {item['stock_tvc']:.0f} | "
                    f"vol {volumen_txt} | sat {item['sat_key'] or 'N/A'}"
                )
            if len(coincidencias) > 50:
                lineas.append(f"... y {len(coincidencias) - 50} coincidencias mas")
            lineas.append("")

        if crear:
            lineas.append("NUEVOS A CREAR:")
            for item in crear[:50]:
                volumen_txt = f"{item['volumen_tvc']:.6f}" if item["volumen_tvc"] is not None else "N/A"
                lineas.append(
                    f"- {item['modelo']} | ref {item['referencia']} | {item['nombre']} | compra {item['precio_compra_tvc']:.2f} | venta {item['precio_venta_tvc']:.2f} | "
                    f"stock {item['stock_tvc']:.0f} | vol {volumen_txt} | "
                    f"sat {item['sat_key'] or 'N/A'} | imagen {'si' if item['tiene_imagen'] else 'no'}"
                )
            if len(crear) > 50:
                lineas.append(f"... y {len(crear) - 50} productos nuevos mas")

        return "\n".join(lineas)

    def crear_producto_nuevo(self, producto):
        nombre = self.obtener_nombre_producto(producto)
        modelo = self.obtener_modelo_producto(producto)
        referencia = self.obtener_clave_referencia_odoo(producto)

        if not modelo:
            self.log(f"Saltado '{nombre}': sin modelo")
            return False

        existentes = self.buscar_producto_odoo(referencia, modelo)
        if existentes:
            self.log(f"Omitido '{nombre}': ya existe en Odoo con clave {referencia}")
            return False

        data = self.construir_data_odoo(producto)
        template_id = self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            "product.template",
            "create",
            [data],
        )
        self.actualizar_proveedor_compra(template_id, producto)
        self.actualizar_stock(template_id, self.obtener_stock_producto(producto))
        self.log(f"Creado: {nombre} (ID {template_id})")
        return True

    def sobrescribir_producto(self, producto):
        nombre = self.obtener_nombre_producto(producto)
        modelo = self.obtener_modelo_producto(producto)
        referencia = self.obtener_clave_referencia_odoo(producto)

        if not modelo:
            self.log(f"Saltado '{nombre}': sin modelo")
            return False

        existentes = self.buscar_producto_odoo(referencia, modelo)
        if not existentes:
            self.log(f"Omitido '{nombre}': no existe en Odoo con clave {referencia}")
            return False

        template_id = existentes[0]["id"]
        data = self.construir_data_odoo(producto)
        self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            "product.template",
            "write",
            [[template_id], data],
        )
        self.actualizar_proveedor_compra(template_id, producto)
        self.actualizar_stock(template_id, self.obtener_stock_producto(producto))
        self.log(f"Sobrescrito: {nombre} (ID {template_id})")
        return True

    def sincronizar_preview_items(self, crear_nuevos=True, sobrescribir_existentes=True):
        resumen = {
            "creados": 0,
            "sobrescritos": 0,
            "errores": 0,
            "omitidos": 0,
        }

        for item in self.preview_cache:
            try:
                accion = item.get("accion")
                if accion == "crear":
                    if not crear_nuevos:
                        resumen["omitidos"] += 1
                        continue
                    if self.crear_producto_nuevo(item["producto"]):
                        resumen["creados"] += 1
                    else:
                        resumen["omitidos"] += 1
                elif accion == "coincidencia":
                    if not sobrescribir_existentes:
                        resumen["omitidos"] += 1
                        continue
                    if self.sobrescribir_producto(item["producto"]):
                        resumen["sobrescritos"] += 1
                    else:
                        resumen["omitidos"] += 1
                else:
                    resumen["omitidos"] += 1
            except Exception as e:
                self.log(f"Error sincronizando producto: {e}")
                resumen["errores"] += 1

        return resumen

    def run_daily_sync(self, modelos):
        carga = self.cargar_productos(modelos)
        self.preview_cache = self.construir_preview()
        crear, coincidencias, saltados = self.separar_preview_acciones()
        sync = self.sincronizar_preview_items(crear_nuevos=True, sobrescribir_existentes=True)
        return {
            "carga": carga,
            "preview": {
                "nuevos": len(crear),
                "coincidencias": len(coincidencias),
                "saltados": len(saltados),
                "no_encontrados": len(self.modelos_no_encontrados),
            },
            "sync": sync,
        }
