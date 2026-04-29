import io
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk

from tvc_sync_core import DEFAULT_MODELS, DISPLAY_CURRENCY, TVCSyncCore


class TVCModelListApp(TVCSyncCore):
    def __init__(self, root):
        self.root = root
        super().__init__()

        self.root.title("Lista de Modelos TVC -> Odoo")
        self.root.geometry("1650x920")

        frame_top = tk.Frame(root, pady=10, padx=10)
        frame_top.pack(fill=tk.X)

        tk.Label(frame_top, text="Modelos TVC (uno por linea):").pack(anchor=tk.W)

        self.txt_modelos = ScrolledText(frame_top, wrap=tk.WORD, height=9)
        self.txt_modelos.pack(fill=tk.X, pady=(5, 10))
        self.txt_modelos.insert("1.0", DEFAULT_MODELS)

        frame_buttons = tk.Frame(frame_top)
        frame_buttons.pack(fill=tk.X)

        btn_cargar = tk.Button(frame_buttons, text="Cargar lista", command=self.actualizar_datos)
        btn_cargar.pack(side=tk.LEFT)

        tk.Label(frame_buttons, text="Ordenar por:").pack(side=tk.LEFT, padx=(20, 5))
        self.combo_orden = ttk.Combobox(
            frame_buttons,
            values=["Titulo (A-Z)", "Precio (Mayor a menor)", "Precio (Menor a mayor)"],
            state="readonly",
            width=20,
        )
        self.combo_orden.pack(side=tk.LEFT)
        self.combo_orden.current(0)

        btn_ordenar = tk.Button(frame_buttons, text="Aplicar", command=self.ordenar_datos)
        btn_ordenar.pack(side=tk.LEFT, padx=5)

        btn_previsualizar = tk.Button(frame_buttons, text="Previsualizar Odoo", command=self.previsualizar_cambios)
        btn_previsualizar.pack(side=tk.LEFT, padx=10)

        btn_enviar = tk.Button(frame_buttons, text="Crear solo nuevos", command=self.enviar_todos)
        btn_enviar.pack(side=tk.LEFT, padx=5)

        btn_sobrescribir = tk.Button(
            frame_buttons,
            text="Sobrescribir existentes",
            command=self.sobrescribir_existentes,
        )
        btn_sobrescribir.pack(side=tk.LEFT, padx=5)

        frame_main = tk.Frame(root)
        frame_main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        frame_grid = tk.Frame(frame_main)
        frame_grid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            frame_grid,
            columns=("TVC_ID", "Modelo", "Referencia", "Titulo", "P_Lista", "P_Desc", "Existencia"),
            show="headings",
        )
        self.tree.heading("TVC_ID", text="ID TVC")
        self.tree.heading("Modelo", text="Modelo TVC")
        self.tree.heading("Referencia", text="Referencia")
        self.tree.heading("Titulo", text="Titulo")
        self.tree.heading("P_Lista", text="Precio Lista")
        self.tree.heading("P_Desc", text="Precio Venta")
        self.tree.heading("Existencia", text="Stock Total")

        self.tree.column("TVC_ID", width=80)
        self.tree.column("Modelo", width=160)
        self.tree.column("Referencia", width=160)
        self.tree.column("Titulo", width=420)
        self.tree.column("P_Lista", width=105)
        self.tree.column("P_Desc", width=125)
        self.tree.column("Existencia", width=95)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.mostrar_detalle)

        frame_side = tk.Frame(frame_main, width=490)
        frame_side.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        frame_side.pack_propagate(False)

        frame_detalle = tk.Frame(frame_side, relief=tk.SUNKEN, borderwidth=2, height=330)
        frame_detalle.pack(fill=tk.X)
        frame_detalle.pack_propagate(False)

        tk.Label(frame_detalle, text="Detalle del Producto", font=("Arial", 12, "bold")).pack(pady=10)

        self.lbl_imagen = tk.Label(frame_detalle, text="[Imagen]")
        self.lbl_imagen.pack(pady=10)

        self.lbl_info = tk.Label(
            frame_detalle,
            text="Selecciona un producto\npara ver mas detalles.",
            justify=tk.LEFT,
            wraplength=450,
        )
        self.lbl_info.pack(pady=10, padx=10, fill=tk.X)

        frame_preview = tk.Frame(frame_side, relief=tk.SUNKEN, borderwidth=2)
        frame_preview.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        tk.Label(frame_preview, text="Preview de Estado", font=("Arial", 12, "bold")).pack(pady=10)

        self.txt_preview = ScrolledText(frame_preview, wrap=tk.WORD, height=24)
        self.txt_preview.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.actualizar_preview(
            "1. Pega tu lista de modelos.\n"
            "2. Haz clic en 'Cargar lista'.\n"
            "3. Usa 'Previsualizar Odoo' para revisar cuales existen o faltan.\n"
            "4. Usa 'Crear solo nuevos' o 'Sobrescribir existentes'.\n"
        )

    def obtener_modelos_desde_ui(self):
        return self.parsear_modelos_texto(self.txt_modelos.get("1.0", tk.END))

    def actualizar_preview(self, texto):
        self.txt_preview.config(state=tk.NORMAL)
        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert(tk.END, texto)
        self.txt_preview.config(state=tk.DISABLED)

    def repintar_tree(self, productos=None):
        for item in self.tree.get_children():
            self.tree.delete(item)

        if productos is None:
            productos = list(self.productos_cache.values())

        for producto in productos:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    producto.get("tvc_id", ""),
                    self.obtener_modelo_producto(producto) or "N/A",
                    self.obtener_referencia_producto(producto) or "N/A",
                    self.obtener_nombre_producto(producto),
                    f"${self.obtener_precio_lista(producto):,.2f}",
                    f"${self.obtener_precio_venta(producto):,.2f}",
                    f"{self.obtener_stock_producto(producto):.0f}",
                ),
            )

    def actualizar_datos(self):
        modelos = self.obtener_modelos_desde_ui()
        if not modelos:
            messagebox.showwarning("Lista vacia", "Pega al menos un modelo en la lista.")
            return

        try:
            summary = self.cargar_productos(modelos)
        except Exception as e:
            messagebox.showerror("Error TVC", f"No fue posible consultar TVC:\n{e}")
            return

        self.repintar_tree()
        self.guardar_modelos_no_encontrados("modelos_no_encontrados_tvc.txt")
        self.actualizar_preview(self.formatear_estado_carga(summary))

    def ordenar_datos(self):
        criterio = self.combo_orden.get()
        lista_productos = list(self.productos_cache.values())

        if criterio == "Titulo (A-Z)":
            lista_productos.sort(key=lambda x: self.obtener_nombre_producto(x).lower())
        elif criterio == "Precio (Mayor a menor)":
            lista_productos.sort(key=lambda x: self.obtener_precio_venta(x), reverse=True)
        elif criterio == "Precio (Menor a mayor)":
            lista_productos.sort(key=lambda x: self.obtener_precio_venta(x))

        self.repintar_tree(lista_productos)

    def mostrar_detalle(self, event):
        seleccion = self.tree.selection()
        if not seleccion:
            return

        valores = self.tree.item(seleccion[0], "values")
        tvc_id = str(valores[0])
        producto = self.productos_cache.get(tvc_id)
        if not producto:
            return

        titulo = self.obtener_nombre_producto(producto)
        modelo = self.obtener_modelo_producto(producto)
        referencia = self.obtener_referencia_producto(producto) or "N/A"
        marca = producto.get("brand", "")
        precio_lista = self.obtener_precio_lista(producto)
        precio_compra = self.obtener_precio_descuento(producto)
        precio_venta = self.obtener_precio_venta(producto)
        stock = self.obtener_stock_producto(producto)
        sat = self.obtener_sat_producto(producto) or "N/A"
        peso = self.obtener_peso_producto(producto)
        volumen = self.obtener_volumen_producto(producto)
        descuentos = self.obtener_descuentos_aplicables(producto)
        descuentos_volumen = self.obtener_descuentos_por_volumen(producto)

        texto_detalle = (
            f"Marca: {marca}\n"
            f"Modelo TVC: {modelo}\n"
            f"Referencia: {referencia}\n"
            f"TVC ID: {producto.get('tvc_id', 'N/A')}\n"
            f"SAT / UNSPSC: {sat}\n"
            f"Precio lista: {precio_lista:.2f} {DISPLAY_CURRENCY}\n"
            f"Precio compra TVC: {precio_compra:.2f} {DISPLAY_CURRENCY}\n"
            f"Precio venta enviado a Odoo: {precio_venta:.2f} {DISPLAY_CURRENCY}\n"
            f"Tipo de cambio actual: {f'{self.last_exchange_rate:.4f}' if self.last_exchange_rate else 'no disponible'}\n"
            f"Stock TVC: {stock:.0f}\n"
            f"Peso pieza: {f'{peso:.3f}' if peso is not None else 'N/A'}\n"
            f"Volumen pieza (m3): {f'{volumen:.6f}' if volumen is not None else 'N/A'}\n"
            f"Descuentos aplicables: {descuentos}\n"
            f"Escalas por volumen: {descuentos_volumen}\n\n"
            f"Descripcion:\n{titulo}"
        )
        self.lbl_info.config(text=texto_detalle)

        url_imagen = self.obtener_url_imagen(producto)
        if not url_imagen:
            self.lbl_imagen.config(image="", text="Sin imagen disponible")
            return

        try:
            img_response = self.session.get(url_imagen, timeout=30)
            img_response.raise_for_status()
            img_data = Image.open(io.BytesIO(img_response.content))
            img_data = img_data.resize((200, 200), Image.LANCZOS)
            foto = ImageTk.PhotoImage(img_data)
            self.lbl_imagen.config(image=foto, text="")
            self.lbl_imagen.image = foto
        except Exception:
            self.lbl_imagen.config(image="", text="Error cargando imagen")

    def previsualizar_cambios(self):
        try:
            self.preview_cache = self.construir_preview()
        except Exception as e:
            self.actualizar_preview(f"Error consultando Odoo:\n{e}")
            return

        self.actualizar_preview(self.formatear_preview())

    def enviar_todos(self):
        if not self.preview_cache:
            self.previsualizar_cambios()
            if not self.preview_cache:
                return

        nuevos, coincidencias, _ = self.separar_preview_acciones()
        mensaje = (
            f"Se crearan {len(nuevos)} productos nuevos.\n"
            f"Ya existen {len(coincidencias)} productos en Odoo.\n\n"
            "Deseas crear solo los nuevos?"
        )
        if not messagebox.askyesno("Confirmar creacion", mensaje):
            return

        resumen_sync = self.sincronizar_preview_items(crear_nuevos=True, sobrescribir_existentes=False)
        resumen = (
            f"Proceso terminado.\n"
            f"Creados: {resumen_sync['creados']}\n"
            f"Errores: {resumen_sync['errores']}\n"
            f"Omitidos por coincidencia: {len(coincidencias)}"
        )
        self.actualizar_preview(f"{self.txt_preview.get('1.0', tk.END).strip()}\n\n{resumen}")
        messagebox.showinfo("Resultado", resumen)

    def sobrescribir_existentes(self):
        if not self.preview_cache:
            self.previsualizar_cambios()
            if not self.preview_cache:
                return

        _, coincidencias, _ = self.separar_preview_acciones()
        nuevos = [item for item in self.preview_cache if item["accion"] == "crear"]

        mensaje = (
            f"Se sobrescribiran {len(coincidencias)} productos existentes.\n"
            f"Se dejaran intactos {len(nuevos)} productos nuevos.\n\n"
            "Deseas continuar?"
        )
        if not messagebox.askyesno("Confirmar sobrescritura", mensaje):
            return

        resumen_sync = self.sincronizar_preview_items(crear_nuevos=False, sobrescribir_existentes=True)
        resumen = (
            f"Proceso terminado.\n"
            f"Sobrescritos: {resumen_sync['sobrescritos']}\n"
            f"Errores: {resumen_sync['errores']}\n"
            f"Nuevos no tocados: {len(nuevos)}"
        )
        self.actualizar_preview(f"{self.txt_preview.get('1.0', tk.END).strip()}\n\n{resumen}")
        messagebox.showinfo("Resultado", resumen)


if __name__ == "__main__":
    ventana = tk.Tk()
    app = TVCModelListApp(ventana)
    ventana.mainloop()
