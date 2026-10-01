import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# ==========================================
# CONEXIÓN A GOOGLE SHEETS
# ==========================================
@st.cache_resource
def get_sheets_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    secretos = json.loads(st.secrets["google_credentials"], strict=False)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(secretos, scope)
    client = gspread.authorize(creds)
    sheet = client.open("Inventario_Negocio")
    return sheet

sh = get_sheets_connection()

# --- FUNCIONES ROBUSTAS PARA PROTEGER LA NUBE ---
def leer_tabla(nombre_pestana):
    try:
        data = sh.worksheet(nombre_pestana).get_all_records()
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()

def asegurar_columnas(nombre_pestana, columnas_esperadas):
    """Verifica que la hoja exista y tenga las columnas necesarias. Si no, las añade sin borrar datos."""
    try:
        ws = sh.worksheet(nombre_pestana)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=nombre_pestana, rows="100", cols="20")
        ws.append_row(columnas_esperadas)
        return

    if ws.row_count > 0:
        header = ws.row_values(1)
        faltantes = [col for col in columnas_esperadas if col not in header]
        if faltantes:
            df = leer_tabla(nombre_pestana)
            for col in faltantes:
                df[col] = "Sin Categoría" # Valor por defecto para no afectar filas viejas
            actualizar_tabla(nombre_pestana, df)
    else:
        ws.append_row(columnas_esperadas)

def escribir_fila(nombre_pestana, fila):
    sh.worksheet(nombre_pestana).append_row(fila)

def actualizar_tabla(nombre_pestana, df):
    worksheet = sh.worksheet(nombre_pestana)
    worksheet.clear()
    if not df.empty:
        worksheet.update([df.columns.values.tolist()] + df.values.tolist())
    else:
        worksheet.update([df.columns.values.tolist()])

# Asegurar estructura con las nuevas columnas (categoria)
asegurar_columnas("insumos", ["nombre", "categoria", "unidad", "costo_unidad", "stock", "stock_minimo"])
asegurar_columnas("recetas", ["id", "nombre", "categoria", "precio_venta"])
asegurar_columnas("receta_ingredientes", ["receta_id", "insumo_nombre", "cantidad"])
asegurar_columnas("tandas", ["id", "receta_nombre", "cantidad_producida", "stock_disponible", "costo_total", "fecha", "notas"])
asegurar_columnas("finanzas", ["tipo", "monto", "fecha", "descripcion"])

# ==========================================
# INTERFAZ DE USUARIO (STREAMLIT)
# ==========================================
st.set_page_config(page_title="Repostería Mágica", layout="wide", page_icon="🍰")

# Estilos CSS para simular las tarjetas y etiquetas del video
st.markdown("""
<style>
.card {
    background-color: #ffffff;
    padding: 20px;
    border-radius: 15px;
    box-shadow: 0 4px 10px rgba(0,0,0,0.05);
    margin-bottom: 15px;
    border-left: 6px solid #FF9F43;
}
.badge-suficiente { background-color: #e6f8eb; color: #20c997; padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 0.8em; }
.badge-bajo { background-color: #fce8e8; color: #dc3545; padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 0.8em; }
.categoria-label { font-size: 0.8em; color: #fd7e14; text-transform: uppercase; font-weight: bold; letter-spacing: 1px; }
</style>
""", unsafe_allow_html=True)

# ENCABEZADO
st.title("🍰 Repostería Mágica")
st.caption("Control de Insumos y Tandas")
st.write("---")

# NAVEGACIÓN ESTILO APP (Pestañas Superiores)
tabs = st.tabs(["📦 Inventario de Insumos", "📖 Recetas", "🍳 Registrar Tanda", "🛒 Lista de Compras", "💰 Ventas/Finanzas", "⚙️ Respaldos y Ajustes"])

# --- SECCION 1: INVENTARIO (Estilo Visual) ---
with tabs[0]:
    busqueda = st.text_input("🔍 Buscar ingrediente...", placeholder="Ej. Leche Condensada")
    
    with st.expander("➕ Nuevo Insumo"):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre del insumo")
            categoria = st.selectbox("Categoría", ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"])
            unidad = st.selectbox("Unidad de medida", ["latas", "paquetes", "g", "ml", "piezas", "Kg", "Litro"])
        with col2:
            costo = st.number_input("Costo por unidad ($)", min_value=0.0, format="%.2f")
            cantidad_comprada = st.number_input("Cantidad disponible/comprada", min_value=0.0)
            stock_minimo = st.number_input("Mínimo sugerido", min_value=0.0)
            
        if st.button("Guardar Insumo", type="primary"):
            if nombre:
                df_insumos = leer_tabla("insumos")
                if not df_insumos.empty and nombre in df_insumos["nombre"].values:
                    idx = df_insumos[df_insumos["nombre"] == nombre].index[0]
                    df_insumos.loc[idx, "stock"] = float(df_insumos.loc[idx, "stock"]) + cantidad_comprada
                    df_insumos.loc[idx, "costo_unidad"] = costo
                    df_insumos.loc[idx, "stock_minimo"] = stock_minimo
                    df_insumos.loc[idx, "categoria"] = categoria
                    actualizar_tabla("insumos", df_insumos)
                else:
                    escribir_fila("insumos", [nombre, categoria, unidad, costo, cantidad_comprada, stock_minimo])
                
                if cantidad_comprada > 0:
                    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    escribir_fila("finanzas", ["Egreso", costo * cantidad_comprada, fecha_actual, f"Compra de {cantidad_comprada} {unidad} de {nombre}"])
                st.success("Insumo actualizado.")
                st.rerun()

    df_insumos = leer_tabla("insumos")
    if not df_insumos.empty:
        if busqueda:
            df_insumos = df_insumos[df_insumos['nombre'].str.contains(busqueda, case=False)]
        
        # Grid de 2 columnas para las tarjetas
        col_t1, col_t2 = st.columns(2)
        for i, row in df_insumos.iterrows():
            stock = float(row.get('stock', 0))
            minimo = float(row.get('stock_minimo', 0))
            estado_clase = "badge-suficiente" if stock > minimo else "badge-bajo"
            estado_texto = "Suficiente" if stock > minimo else "Crítico"
            
            tarjeta_html = f"""
            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <span class="categoria-label">{row.get('categoria', 'OTROS')}</span>
                    <span class="{estado_clase}">{estado_texto}</span>
                </div>
                <h4 style="margin: 0 0 15px 0; color: #333;">{row['nombre']}</h4>
                <div style="display: flex; gap: 40px; margin-bottom: 15px;">
                    <div><span style="color: #888; font-size: 0.9em;">Disponible</span><br><b style="font-size: 1.2em;">{stock}</b> <small>{row['unidad']}</small></div>
                    <div><span style="color: #888; font-size: 0.9em;">Mínimo sugerido</span><br><b style="font-size: 1.2em;">{minimo}</b> <small>{row['unidad']}</small></div>
                </div>
                <div style="color: #aaa; font-size: 0.85em;">Costo: ${row['costo_unidad']} / {row['unidad']}</div>
            </div>
            """
            if i % 2 == 0:
                col_t1.markdown(tarjeta_html, unsafe_allow_html=True)
            else:
                col_t2.markdown(tarjeta_html, unsafe_allow_html=True)
    else:
        st.info("No hay insumos.")

# --- SECCION 2: RECETAS (Con cálculo de costos) ---
with tabs[1]:
    with st.expander("➕ Nueva Receta"):
        df_insumos = leer_tabla("insumos")
        nombres_insumos = df_insumos["nombre"].tolist() if not df_insumos.empty else []
        
        if 'ing_temp' not in st.session_state:
            st.session_state.ing_temp = []
            
        nombre_receta = st.text_input("Nombre del postre")
        categoria_receta = st.selectbox("Categoría", ["Carlotas", "Pays", "Pasteles", "Galletas", "Bebidas"])
        precio_venta = st.number_input("Precio de venta al público ($)", min_value=0.0, format="%.2f")
        
        col_i1, col_i2 = st.columns(2)
        with col_i1:
            insumo_sel = st.selectbox("Selecciona Insumo", nombres_insumos if nombres_insumos else ["Vacío"])
        with col_i2:
            cant_insumo = st.number_input("Cantidad para 1 tanda", min_value=0.0)
            
        if st.button("Añadir Ingrediente a la Receta"):
            if insumo_sel != "Vacío":
                unidad_ins = df_insumos[df_insumos['nombre'] == insumo_sel]['unidad'].values[0]
                st.session_state.ing_temp.append({"nombre": insumo_sel, "cantidad": cant_insumo, "unidad": unidad_ins})
                st.success(f"{insumo_sel} añadido.")
        
        if st.session_state.ing_temp:
            st.table(pd.DataFrame(st.session_state.ing_temp))
            if st.button("Guardar Receta Completa", type="primary"):
                if nombre_receta:
                    df_recetas = leer_tabla("recetas")
                    nuevo_id = 1 if df_recetas.empty else len(df_recetas) + 1
                    escribir_fila("recetas", [nuevo_id, nombre_receta, categoria_receta, precio_venta])
                    for ing in st.session_state.ing_temp:
                        escribir_fila("receta_ingredientes", [nuevo_id, ing['nombre'], ing['cantidad']])
                    st.session_state.ing_temp = []
                    st.rerun()

    df_recetas = leer_tabla("recetas")
    df_ri = leer_tabla("receta_ingredientes")
    
    if not df_recetas.empty:
        col_r1, col_r2 = st.columns(2)
        for i, row in df_recetas.iterrows():
            r_id = row['id']
            ingredientes = df_ri[df_ri['receta_id'] == r_id] if not df_ri.empty else pd.DataFrame()
            
            costo_estimado = 0
            html_ing = ""
            if not ingredientes.empty and not df_insumos.empty:
                for _, ing in ingredientes.iterrows():
                    nom_ins = ing['insumo_nombre']
                    cant_ins = float(ing['cantidad'])
                    ins_data = df_insumos[df_insumos['nombre'] == nom_ins]
                    if not ins_data.empty:
                        costo_u = float(ins_data.iloc[0]['costo_unidad'])
                        unidad = ins_data.iloc[0]['unidad']
                        costo_estimado += (costo_u * cant_ins)
                        html_ing += f"<tr><td style='padding: 4px 0; color: #555;'>{nom_ins}</td><td style='text-align:right; color: #555;'><b>{cant_ins}</b> {unidad}</td></tr>"
            
            tarjeta_receta = f"""
            <div class="card" style="border-left-color: #e83e8c;">
                <div style="display: flex; justify-content: space-between;">
                    <h3 style="margin: 0; color: #333;">{row['nombre']}</h3>
                    <div style="text-align: right; color: #20c997; line-height: 1.2;">
                        <span style="font-size: 0.8em; color: #888;">Costo estimado</span><br>
                        <b>${costo_estimado:.2f}</b>
                    </div>
                </div>
                <span class="categoria-label" style="color: #e83e8c;">{row.get('categoria', 'POSTRES')}</span>
                <div style="margin-top: 20px; background-color: #f8f9fa; padding: 15px; border-radius: 8px;">
                    <p style="font-size: 0.8em; color: #888; font-weight: bold; margin-top:0;">INGREDIENTES POR 1 TANDA:</p>
                    <table style="width: 100%; font-size: 0.95em;">{html_ing}</table>
                </div>
            </div>
            """
            if i % 2 == 0:
                col_r1.markdown(tarjeta_receta, unsafe_allow_html=True)
            else:
                col_r2.markdown(tarjeta_receta, unsafe_allow_html=True)

# --- SECCION 3: PRODUCCION (Modal Interactivo del Video) ---
with tabs[2]:
    st.markdown("### 📝 Registrar Tanda Producida")
    df_recetas = leer_tabla("recetas")
    
    if not df_recetas.empty:
        receta_sel = st.selectbox("SELECCIONAR RECETA", ["-- Selecciona un postre --"] + df_recetas["nombre"].tolist())
        
        if receta_sel != "-- Selecciona un postre --":
            cantidad_preparar = st.number_input("NÚMERO DE TANDAS PREPARADAS", min_value=1, step=1, value=1)
            
            st.markdown("#### Se descontará del inventario:")
            st.markdown("<div style='background-color: #f8f9fa; padding: 15px; border-radius: 10px;'>", unsafe_allow_html=True)
            
            receta_row = df_recetas[df_recetas["nombre"] == receta_sel].iloc[0]
            df_ri = leer_tabla("receta_ingredientes")
            ingredientes_receta = df_ri[df_ri["receta_id"] == receta_row["id"]]
            df_insumos = leer_tabla("insumos")
            
            suficiente = True
            costo_total = 0.0
            
            for _, row in ingredientes_receta.iterrows():
                insumo_nombre = row["insumo_nombre"]
                cant_necesaria = float(row["cantidad"]) * cantidad_preparar
                insumo_data = df_insumos[df_insumos["nombre"] == insumo_nombre]
                
                if not insumo_data.empty:
                    unidad = insumo_data.iloc[0]['unidad']
                    stock_actual = float(insumo_data.iloc[0]['stock'])
                    costo_u = float(insumo_data.iloc[0]['costo_unidad'])
                    costo_total += (cant_necesaria * costo_u)
                    
                    if stock_actual < cant_necesaria:
                        st.markdown(f"<p style='color:#dc3545; margin:5px 0;'>❌ <b>{insumo_nombre}</b>: Necesitas {cant_necesaria} {unidad} <i>(Solo tienes {stock_actual})</i></p>", unsafe_allow_html=True)
                        suficiente = False
                    else:
                        st.markdown(f"<p style='color:#20c997; margin:5px 0;'>✅ <b>{insumo_nombre}</b>: {cant_necesaria} {unidad}</p>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            
            st.write("")
            if st.button("Confirmar Tanda", type="primary", disabled=not suficiente, use_container_width=True):
                if suficiente:
                    for _, row in ingredientes_receta.iterrows():
                        ins_nom = row["insumo_nombre"]
                        cant_nec = float(row["cantidad"]) * cantidad_preparar
                        idx_ins = df_insumos[df_insumos["nombre"] == ins_nom].index[0]
                        df_insumos.loc[idx_ins, "stock"] = float(df_insumos.loc[idx_ins, "stock"]) - cant_nec
                    actualizar_tabla("insumos", df_insumos)
                    
                    df_tandas = leer_tabla("tandas")
                    tanda_id = 1 if df_tandas.empty else len(df_tandas) + 1
                    escribir_fila("tandas", [tanda_id, receta_sel, cantidad_preparar, cantidad_preparar, costo_total, datetime.now().strftime("%Y-%m-%d %H:%M"), ""])
                    st.success("Tanda registrada y descontada del inventario.")
                    st.rerun()

# --- SECCION 4: LISTA DE COMPRAS (Nueva Pestaña) ---
with tabs[3]:
    st.markdown("### 📋 Lista Automática de Compras")
    st.caption("Insumos que han alcanzado o bajado de su nivel mínimo de seguridad.")
    
    df_insumos = leer_tabla("insumos")
    if not df_insumos.empty:
        df_insumos['stock'] = pd.to_numeric(df_insumos['stock'])
        df_insumos['stock_minimo'] = pd.to_numeric(df_insumos['stock_minimo'])
        faltantes = df_insumos[df_insumos['stock'] <= df_insumos['stock_minimo']]
        
        if faltantes.empty:
            st.success("✔️ ¡Todo en orden! Tienes suficiente inventario de todos tus insumos para seguir horneando.")
        else:
            lista_txt = "LISTA DE COMPRAS - REPOSTERÍA\n" + "-"*30 + "\n"
            for _, row in faltantes.iterrows():
                comprar = row['stock_minimo'] - row['stock'] if row['stock'] < row['stock_minimo'] else row['stock_minimo']
                st.warning(f"🛒 **{row['nombre']}**: Te quedan {row['stock']} {row['unidad']}. (Sugerido comprar mínimo: {comprar} {row['unidad']})")
                lista_txt += f"- {row['nombre']} ({comprar} {row['unidad']})\n"
            
            st.download_button("📥 Descargar Lista (.txt)", data=lista_txt, file_name="lista_compras.txt")

# --- SECCION 5: VENTAS Y FINANZAS (Conservadas de tu código original) ---
with tabs[4]:
    col_v, col_f = st.columns(2)
    with col_v:
        st.subheader("🛒 Punto de Venta")
        df_tandas = leer_tabla("tandas")
        if not df_tandas.empty and "stock_disponible" in df_tandas.columns:
            df_disponibles = df_tandas[pd.to_numeric(df_tandas["stock_disponible"]) > 0]
            if not df_disponibles.empty:
                tanda_id_sel = st.selectbox("Lote a vender", df_disponibles["id"].tolist())
                tanda_row = df_disponibles[df_disponibles["id"] == tanda_id_sel].iloc[0]
                max_stock = int(float(tanda_row["stock_disponible"]))
                
                cant_vender = st.number_input(f"Cantidad a vender ({tanda_row['receta_nombre']})", min_value=1, max_value=max_stock)
                
                if st.button("Registrar Venta", type="primary"):
                    df_recetas = leer_tabla("recetas")
                    precio_v = float(df_recetas[df_recetas["nombre"] == tanda_row["receta_nombre"]].iloc[0]["precio_venta"])
                    ingreso = cant_vender * precio_v
                    
                    idx_tanda = df_tandas[df_tandas["id"] == tanda_id_sel].index[0]
                    df_tandas.loc[idx_tanda, "stock_disponible"] = float(df_tandas.loc[idx_tanda, "stock_disponible"]) - cant_vender
                    actualizar_tabla("tandas", df_tandas)
                    escribir_fila("finanzas", ["Ingreso", ingreso, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Venta de {cant_vender} {tanda_row['receta_nombre']}"])
                    st.success(f"Venta registrada. +${ingreso:.2f}")
                    st.rerun()
            else:
                st.info("No hay postres disponibles para vender.")
                
    with col_f:
        st.subheader("📈 Resumen Financiero")
        df_finanzas = leer_tabla("finanzas")
        if not df_finanzas.empty:
            ingresos = df_finanzas[df_finanzas['tipo'] == 'Ingreso']['monto'].astype(float).sum()
            egresos = df_finanzas[df_finanzas['tipo'] == 'Egreso']['monto'].astype(float).sum()
            
            st.metric("Balance General", f"${ingresos - egresos:.2f}")
            st.dataframe(df_finanzas, use_container_width=True, height=200)

# --- SECCION 6: RESPALDOS Y AJUSTES (Nueva Pestaña) ---
with tabs[5]:
    st.markdown("### ⚙️ Copia de Seguridad y Datos")
    st.write("Guarda o restaura toda tu información fácilmente.")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("<div class='card' style='border-left-color: #17a2b8;'>", unsafe_allow_html=True)
        st.markdown("#### 💾 Exportar Copia de Seguridad")
        st.write("Descarga un archivo JSON con todas tus recetas e insumos.")
        if st.button("Generar Respaldo"):
            with st.spinner("Compilando..."):
                datos = {
                    "insumos": leer_tabla("insumos").to_dict(orient="records"),
                    "recetas": leer_tabla("recetas").to_dict(orient="records"),
                    "receta_ingredientes": leer_tabla("receta_ingredientes").to_dict(orient="records")
                }
                st.download_button("⬇️ Descargar Archivo", data=json.dumps(datos, indent=4), file_name="respaldo_magico.json", mime="application/json")
        st.markdown("</div>", unsafe_allow_html=True)
                
    with col_b2:
        st.markdown("<div class='card' style='border-left-color: #dc3545;'>", unsafe_allow_html=True)
        st.markdown("#### ⚠️ Restablecer Datos Iniciales")
        st.write("Borra todo y deja las tablas en blanco. **Irreversible**.")
        confirmacion = st.text_input("Escribe 'BORRAR' para confirmar")
        if st.button("Formatear Sistema", type="primary") and confirmacion == "BORRAR":
            actualizar_tabla("insumos", pd.DataFrame(columns=["nombre", "categoria", "unidad", "costo_unidad", "stock", "stock_minimo"]))
            actualizar_tabla("recetas", pd.DataFrame(columns=["id", "nombre", "categoria", "precio_venta"]))
            actualizar_tabla("receta_ingredientes", pd.DataFrame(columns=["receta_id", "insumo_nombre", "cantidad"]))
            actualizar_tabla("tandas", pd.DataFrame(columns=["id", "receta_nombre", "cantidad_producida", "stock_disponible", "costo_total", "fecha", "notas"]))
            actualizar_tabla("finanzas", pd.DataFrame(columns=["tipo", "monto", "fecha", "descripcion"]))
            st.success("Sistema restablecido de fábrica.")
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
