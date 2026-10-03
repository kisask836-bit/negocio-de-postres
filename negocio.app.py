import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# ==========================================
# CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ==========================================
st.set_page_config(page_title="Lady Pays", layout="wide", page_icon="💎")

st.markdown("""
<style>
    .stApp { background-color: #fafafa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border: 1px solid #e0e0e0; }
    div[data-testid="stExpander"] { background-color: #ffffff; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# CONEXIÓN OPTIMIZADA A GOOGLE SHEETS
# ==========================================
@st.cache_resource
def get_sheets_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    secretos = json.loads(st.secrets["google_credentials"], strict=False)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(secretos, scope)
    client = gspread.authorize(creds)
    return client.open("Inventario_Negocio")

sh = get_sheets_connection()

# CACHÉ DE DATOS: Evita llamadas innecesarias a Google Sheets al cambiar de pestañas
@st.cache_data(ttl=300)
def cargar_tabla(nombre_pestana):
    try:
        ws = sh.worksheet(nombre_pestana)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception:
        return pd.DataFrame()

def recargar_cache():
    st.cache_data.clear()

def escribir_fila(nombre_pestana, fila):
    try:
        sh.worksheet(nombre_pestana).append_row(fila)
        recargar_cache()
    except Exception as e:
        st.error(f"Error al escribir en {nombre_pestana}: {e}")

def actualizar_tabla(nombre_pestana, df):
    try:
        worksheet = sh.worksheet(nombre_pestana)
        worksheet.clear()
        if not df.empty:
            df_limpio = df.fillna("")
            worksheet.update([df_limpio.columns.values.tolist()] + df_limpio.values.tolist())
        else:
            worksheet.update([df.columns.values.tolist()])
        recargar_cache()
    except Exception as e:
        st.error(f"Error al actualizar {nombre_pestana}: {e}")

def obtener_nuevo_id(df):
    if df.empty or 'id' not in df.columns or df['id'].dropna().empty:
        return 1
    try:
        return int(pd.to_numeric(df['id'], errors='coerce').max()) + 1
    except Exception:
        return 1

# --- INICIALIZACIÓN UNIFICADA DE ESTRUCTURA ---
@st.cache_resource
def inicializar_base_datos():
    tablas = {
        "insumos": ["nombre", "categoria", "unidad", "costo_unidad", "stock", "stock_minimo"],
        "recetas": ["id", "nombre", "categoria", "precio_venta"],
        "receta_ingredientes": ["receta_id", "insumo_nombre", "cantidad"],
        "tandas": ["id", "receta_nombre", "cantidad_producida", "stock_disponible", "costo_total", "fecha", "notas"],
        "finanzas": ["id", "tipo", "monto", "fecha", "descripcion"],
        "mermas": ["id", "tipo", "nombre", "cantidad", "unidad", "costo_estimado", "fecha", "motivo"]
    }
    try:
        ws_existentes = {ws.title: ws for ws in sh.worksheets()}
    except Exception:
        return False

    for nombre, columnas in tablas.items():
        if nombre not in ws_existentes:
            ws = sh.add_worksheet(title=nombre, rows="100", cols="20")
            ws.append_row(columnas)
        else:
            ws = ws_existentes[nombre]
            header = ws.row_values(1) if ws.row_count > 0 else []
            if not header:
                ws.append_row(columnas)
    return True

inicializar_base_datos()

# ==========================================
# CABECERA Y CONTROL GENERAL
# ==========================================
col_h1, col_h2 = st.columns([4, 1])
with col_h1:
    st.title("💎 Lady Pays")
    st.caption("Sistema de Control de Insumos, Recetas, Producción y Finanzas")
with col_h2:
    st.write("")
    if st.button("🔄 Sincronizar Datos", help="Presiona si actualizaste la hoja manualmente en Google Sheets"):
        recargar_cache()
        st.rerun()

st.write("---")

tabs = st.tabs([
    "📦 Inventario de Insumos", 
    "📖 Recetas", 
    "🍳 Registrar Tanda", 
    "🛒 Lista de Compras", 
    "💰 Ventas/Finanzas", 
    "🗑️ Mermas", 
    "⚙️ Respaldos y Ajustes"
])

# ==========================================
# 1. INVENTARIO DE INSUMOS
# ==========================================
with tabs[0]:
    df_insumos = cargar_tabla("insumos")
    
    col_k1, col_k2, col_k3 = st.columns(3)
    if not df_insumos.empty:
        df_insumos['stock'] = pd.to_numeric(df_insumos['stock'], errors='coerce').fillna(0)
        df_insumos['stock_minimo'] = pd.to_numeric(df_insumos['stock_minimo'], errors='coerce').fillna(0)
        df_insumos['costo_unidad'] = pd.to_numeric(df_insumos['costo_unidad'], errors='coerce').fillna(0)
        
        criticos = (df_insumos['stock'] <= df_insumos['stock_minimo']).sum()
        valor_inv = (df_insumos['stock'] * df_insumos['costo_unidad']).sum()
        
        col_k1.metric("Total Insumos", len(df_insumos))
        col_k2.metric("Insumos en Stock Crítico", criticos, delta_color="inverse")
        col_k3.metric("Valor del Inventario", f"${valor_inv:,.2f}")

    sub_t1, sub_t2, sub_t3 = st.tabs(["📋 Lista e Insumos", "➕ Agregar Insumo", "✏️ Editar / Eliminar Insumo"])
    
    with sub_t1:
        busqueda = st.text_input("🔍 Buscar insumo...", placeholder="Ej. Leche Condensada")
        if not df_insumos.empty:
            df_mostrar = df_insumos.copy()
            if busqueda:
                df_mostrar = df_mostrar[df_mostrar['nombre'].str.contains(busqueda, case=False, na=False)]
            
            st.dataframe(
                df_mostrar,
                use_container_width=True,
                column_config={
                    "costo_unidad": st.column_config.NumberColumn("Costo/Unidad", format="$%.2f"),
                    "stock": st.column_config.NumberColumn("Stock Actual"),
                    "stock_minimo": st.column_config.NumberColumn("Stock Mínimo")
                }
            )
        else:
            st.info("No hay insumos registrados.")

    with sub_t2:
        with st.form("form_nuevo_insumo", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                nombre = st.text_input("Nombre del insumo").strip()
                categoria = st.selectbox("Categoría", ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"])
                unidad = st.selectbox("Unidad de medida", ["latas", "paquetes", "g", "ml", "piezas", "Kg", "Litro"])
            with col2:
                costo = st.number_input("Costo por unidad ($)", min_value=0.0, format="%.2f")
                cantidad_comprada = st.number_input("Cantidad a agregar al Stock", min_value=0.0)
                stock_minimo = st.number_input("Stock mínimo sugerido", min_value=0.0)
            
            if st.form_submit_button("Guardar Insumo", type="primary"):
                if nombre:
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
                        df_fin = cargar_tabla("finanzas")
                        nuevo_id_fin = obtener_nuevo_id(df_fin)
                        escribir_fila("finanzas", [nuevo_id_fin, "Egreso", costo * cantidad_comprada, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Compra de {cantidad_comprada} {unidad} de {nombre}"])
                    
                    st.success(f"Insumo '{nombre}' guardado correctamente.")
                    st.rerun()

    with sub_t3:
        if not df_insumos.empty:
            insumo_sel = st.selectbox("Selecciona un insumo para modificar o borrar", df_insumos["nombre"].tolist())
            fila_ins = df_insumos[df_insumos["nombre"] == insumo_sel].iloc[0]
            
            with st.form("form_edit_insumo"):
                c1, c2 = st.columns(2)
                with c1:
                    e_nombre = st.text_input("Nombre", value=str(fila_ins["nombre"]))
                    e_cat = st.selectbox("Categoría", ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"], index=["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"].index(fila_ins["categoria"]) if fila_ins["categoria"] in ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"] else 4)
                    e_uni = st.text_input("Unidad", value=str(fila_ins["unidad"]))
                with c2:
                    e_costo = st.number_input("Costo Unitario ($)", min_value=0.0, value=float(fila_ins["costo_unidad"]), format="%.2f")
                    e_stock = st.number_input("Stock Actual Exacto", min_value=0.0, value=float(fila_ins["stock"]))
                    e_min = st.number_input("Stock Mínimo", min_value=0.0, value=float(fila_ins["stock_minimo"]))
                
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    btn_guardar = st.form_submit_button("💾 Guardar Cambios", type="primary")
                with col_btn2:
                    btn_borrar = st.form_submit_button("🗑️ Eliminar Insumo")
                
                if btn_guardar:
                    idx = df_insumos[df_insumos["nombre"] == insumo_sel].index[0]
                    df_insumos.loc[idx] = [e_nombre, e_cat, e_uni, e_costo, e_stock, e_min]
                    actualizar_tabla("insumos", df_insumos)
                    st.success("Insumo actualizado.")
                    st.rerun()
                
                if btn_borrar:
                    df_nuevo = df_insumos[df_insumos["nombre"] != insumo_sel]
                    actualizar_tabla("insumos", df_nuevo)
                    st.warning(f"Insumo '{insumo_sel}' eliminado.")
                    st.rerun()

# ==========================================
# 2. RECETAS
# ==========================================
with tabs[1]:
    df_recetas = cargar_tabla("recetas")
    df_ri = cargar_tabla("receta_ingredientes")
    df_insumos = cargar_tabla("insumos")
    
    sub_r1, sub_r2, sub_r3 = st.tabs(["📖 Ver Recetas", "➕ Nueva Receta", "✏️ Editar / Eliminar Receta"])
    
    with sub_r1:
        if not df_recetas.empty:
            for _, r in df_recetas.iterrows():
                with st.expander(f"🍰 {r['nombre']} ({r.get('categoria', 'POSTRES')}) - Precio Venta: ${float(r.get('precio_venta', 0)):.2f}"):
                    ingreds = df_ri[df_ri['receta_id'] == r['id']] if not df_ri.empty else pd.DataFrame()
                    costo_estimado = 0.0
                    
                    if not ingreds.empty:
                        ingreds_display = []
                        for _, ing in ingreds.iterrows():
                            ins_nom = ing['insumo_nombre']
                            cant = float(ing['cantidad'])
                            ins_data = df_insumos[df_insumos['nombre'] == ins_nom] if not df_insumos.empty else pd.DataFrame()
                            
                            costo_u = float(ins_data.iloc[0]['costo_unidad']) if not ins_data.empty else 0.0
                            unidad = ins_data.iloc[0]['unidad'] if not ins_data.empty else ""
                            costo_item = costo_u * cant
                            costo_estimado += costo_item
                            
                            ingreds_display.append({"Ingrediente": ins_nom, "Cantidad": cant, "Unidad": unidad, "Costo Estimado": f"${costo_item:.2f}"})
                        
                        st.table(pd.DataFrame(ingreds_display))
                    
                    p_venta = float(r.get('precio_venta', 0))
                    ganancia = p_venta - costo_estimado
                    col_m1, col_m2, col_m3 = st.columns(3)
                    col_m1.metric("Costo Insumos (1 Tanda)", f"${costo_estimado:.2f}")
                    col_m2.metric("Precio de Venta", f"${p_venta:.2f}")
                    col_m3.metric("Margen / Ganancia Estimada", f"${ganancia:.2f}")
        else:
            st.info("No hay recetas registradas.")

    with sub_r2:
        if 'ing_temp' not in st.session_state:
            st.session_state.ing_temp = []

        st.subheader("Datos Generales")
        c_r1, c_r2, c_r3 = st.columns(3)
        nom_rec = c_r1.text_input("Nombre de la receta")
        cat_rec = c_r2.text_input("Categoría", value="PAYS")
        p_venta_rec = c_r3.number_input("Precio de Venta ($)", min_value=0.0, format="%.2f")

        st.markdown("---")
        st.subheader("Agregar Ingredientes")
        lista_ins = df_insumos["nombre"].tolist() if not df_insumos.empty else []
        
        ci1, ci2, ci3 = st.columns([2, 2, 1])
        ins_sel = ci1.selectbox("Insumo", lista_ins if lista_ins else ["Sin Insumos"])
        cant_sel = ci2.number_input("Cantidad para 1 tanda", min_value=0.0, step=0.1)
        
        if ci3.button("➕ Añadir"):
            if ins_sel and ins_sel != "Sin Insumos" and cant_sel > 0:
                st.session_state.ing_temp.append({"insumo_nombre": ins_sel, "cantidad": cant_sel})
                st.success(f"{ins_sel} agregado a la lista temporal.")

        if st.session_state.ing_temp:
            st.table(pd.DataFrame(st.session_state.ing_temp))
            col_b_g, col_b_c = st.columns(2)
            if col_b_g.button("💾 Guardar Receta Completa", type="primary"):
                if nom_rec:
                    nuevo_id = obtener_nuevo_id(df_recetas)
                    escribir_fila("recetas", [nuevo_id, nom_rec, cat_rec.upper(), p_venta_rec])
                    for ing in st.session_state.ing_temp:
                        escribir_fila("receta_ingredientes", [nuevo_id, ing['insumo_nombre'], ing['cantidad']])
                    st.session_state.ing_temp = []
                    st.success("Receta guardada exitosamente.")
                    st.rerun()
                else:
                    st.error("Ingresa el nombre de la receta.")
            if col_b_c.button("Limpiar Ingredientes"):
                st.session_state.ing_temp = []
                st.rerun()

    with sub_r3:
        if not df_recetas.empty:
            receta_edit_id = st.selectbox("Selecciona la receta a modificar", df_recetas["id"].tolist(), format_func=lambda x: f"{x} - {df_recetas[df_recetas['id']==x].iloc[0]['nombre']}")
            rec_row = df_recetas[df_recetas["id"] == receta_edit_id].iloc[0]
            
            with st.form("form_edit_receta"):
                ce1, ce2, ce3 = st.columns(3)
                er_nombre = ce1.text_input("Nombre Receta", value=str(rec_row["nombre"]))
                er_cat = ce2.text_input("Categoría", value=str(rec_row["categoria"]))
                er_precio = ce3.number_input("Precio Venta ($)", value=float(rec_row["precio_venta"]), min_value=0.0)
                
                b_edit_rec = st.form_submit_button("💾 Actualizar Encabezado Receta", type="primary")
                b_del_rec = st.form_submit_button("🗑️ Eliminar Receta Completa")
                
                if b_edit_rec:
                    idx = df_recetas[df_recetas["id"] == receta_edit_id].index[0]
                    df_recetas.loc[idx, "nombre"] = er_nombre
                    df_recetas.loc[idx, "categoria"] = er_cat
                    df_recetas.loc[idx, "precio_venta"] = er_precio
                    actualizar_tabla("recetas", df_recetas)
                    st.success("Datos de receta actualizados.")
                    st.rerun()
                
                if b_del_rec:
                    df_rec_nueva = df_recetas[df_recetas["id"] != receta_edit_id]
                    df_ri_nueva = df_ri[df_ri["receta_id"] != receta_edit_id]
                    actualizar_tabla("recetas", df_rec_nueva)
                    actualizar_tabla("receta_ingredientes", df_ri_nueva)
                    st.warning("Receta eliminada.")
                    st.rerun()

# ==========================================
# 3. REGISTRAR TANDA (PRODUCCIÓN)
# ==========================================
with tabs[2]:
    df_recetas = cargar_tabla("recetas")
    df_insumos = cargar_tabla("insumos")
    df_tandas = cargar_tabla("tandas")
    
    st.subheader("📝 Registrar Tanda Producida")
    if not df_recetas.empty:
        c_t1, c_t2 = st.columns(2)
        receta_sel = c_t1.selectbox("Seleccionar Receta", df_recetas["nombre"].tolist())
        cant_preparar = c_t2.number_input("Número de Tandas a Preparar", min_value=1, step=1, value=1)
        notas_tanda = st.text_input("Notas de la tanda (Opcional)", placeholder="Ej. Lote horneado por la mañana")

        if receta_sel:
            receta_row = df_recetas[df_recetas["nombre"] == receta_sel].iloc[0]
            df_ri = cargar_tabla("receta_ingredientes")
            ingredientes = df_ri[df_ri["receta_id"] == receta_row["id"]]
            
            st.markdown("#### Verificación de Insumos:")
            suficiente = True
            costo_total_tanda = 0.0
            
            for _, row in ingredientes.iterrows():
                ins_nom = row["insumo_nombre"]
                cant_nec = float(row["cantidad"]) * cant_preparar
                ins_data = df_insumos[df_insumos["nombre"] == ins_nom] if not df_insumos.empty else pd.DataFrame()
                
                if not ins_data.empty:
                    stock_act = float(ins_data.iloc[0]['stock'])
                    costo_u = float(ins_data.iloc[0]['costo_unidad'])
                    unidad = ins_data.iloc[0]['unidad']
                    costo_total_tanda += (cant_nec * costo_u)
                    
                    if stock_act < cant_nec:
                        st.error(f"❌ {ins_nom}: Necesitas {cant_nec} {unidad} (Disponible: {stock_act})")
                        suficiente = False
                    else:
                        st.success(f"✅ {ins_nom}: {cant_nec} {unidad}")

            if st.button("🍳 Confirmar y Producir Tanda", type="primary", disabled=not suficiente):
                for _, row in ingredientes.iterrows():
                    ins_nom = row["insumo_nombre"]
                    cant_nec = float(row["cantidad"]) * cant_preparar
                    idx_ins = df_insumos[df_insumos["nombre"] == ins_nom].index[0]
                    df_insumos.loc[idx_ins, "stock"] = float(df_insumos.loc[idx_ins, "stock"]) - cant_nec
                
                actualizar_tabla("insumos", df_insumos)
                
                nuevo_tanda_id = obtener_nuevo_id(df_tandas)
                fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                escribir_fila("tandas", [nuevo_tanda_id, receta_sel, cant_preparar, cant_preparar, costo_total_tanda, fecha_str, notas_tanda])
                
                st.success("Tanda registrada e inventario descontado.")
                st.rerun()
    else:
        st.info("No hay recetas registradas para producir.")

    st.write("---")
    st.subheader("✏️ Modificar / Corregir Stock de Tandas Registradas")
    if not df_tandas.empty:
        tanda_edit_id = st.selectbox("Selecciona Lote / Tanda a modificar", df_tandas["id"].tolist(), format_func=lambda x: f"Lote #{x} - {df_tandas[df_tandas['id']==x].iloc[0]['receta_nombre']} ({df_tandas[df_tandas['id']==x].iloc[0]['fecha']})")
        fila_tanda = df_tandas[df_tandas["id"] == tanda_edit_id].iloc[0]
        
        with st.form("form_edit_tanda"):
            ct1, ct2, ct3 = st.columns(3)
            e_disp = ct1.number_input("Stock Disponible Actual", min_value=0.0, value=float(fila_tanda["stock_disponible"]))
            e_notas = ct2.text_input("Notas", value=str(fila_tanda["notas"]))
            e_costo_t = ct3.number_input("Costo Total Tanda ($)", min_value=0.0, value=float(fila_tanda["costo_total"]))
            
            cb1, cb2 = st.columns(2)
            if cb1.form_submit_button("💾 Actualizar Tanda", type="primary"):
                idx_t = df_tandas[df_tandas["id"] == tanda_edit_id].index[0]
                df_tandas.loc[idx_t, "stock_disponible"] = e_disp
                df_tandas.loc[idx_t, "notas"] = e_notas
                df_tandas.loc[idx_t, "costo_total"] = e_costo_t
                actualizar_tabla("tandas", df_tandas)
                st.success("Tanda actualizada.")
                st.rerun()
                
            if cb2.form_submit_button("🗑️ Borrar Registro de Tanda"):
                df_t_nueva = df_tandas[df_tandas["id"] != tanda_edit_id]
                actualizar_tabla("tandas", df_t_nueva)
                st.warning("Registro de tanda eliminado.")
                st.rerun()

# ==========================================
# 4. LISTA DE COMPRAS
# ==========================================
with tabs[3]:
    st.subheader("📋 Lista Automática de Compras")
    df_insumos = cargar_tabla("insumos")
    
    if not df_insumos.empty:
        df_insumos['stock'] = pd.to_numeric(df_insumos['stock'], errors='coerce').fillna(0)
        df_insumos['stock_minimo'] = pd.to_numeric(df_insumos['stock_minimo'], errors='coerce').fillna(0)
        df_insumos['costo_unidad'] = pd.to_numeric(df_insumos['costo_unidad'], errors='coerce').fillna(0)
        
        faltantes = df_insumos[df_insumos['stock'] <= df_insumos['stock_minimo']].copy()
        
        if faltantes.empty:
            st.success("✔️ ¡Inventario completo! Todos los insumos están sobre el nivel mínimo.")
        else:
            faltantes['sugerido_comprar'] = faltantes['stock_minimo'] - faltantes['stock']
            faltantes['sugerido_comprar'] = faltantes['sugerido_comprar'].apply(lambda x: max(x, 1))
            faltantes['costo_estimado_compra'] = faltantes['sugerido_comprar'] * faltantes['costo_unidad']
            
            st.dataframe(
                faltantes[['nombre', 'categoria', 'stock', 'stock_minimo', 'sugerido_comprar', 'unidad', 'costo_estimado_compra']],
                use_container_width=True,
                column_config={
                    "stock": "Stock Actual",
                    "stock_minimo": "Mínimo",
                    "sugerido_comprar": "Sugerido Comprar",
                    "costo_estimado_compra": st.column_config.NumberColumn("Costo Estimado", format="$%.2f")
                }
            )
            
            txt_compras = "LISTA DE COMPRAS - LADY PAYS\n" + "-"*35 + "\n"
            for _, f in faltantes.iterrows():
                txt_compras += f"- {f['nombre']}: Comprar {f['sugerido_comprar']} {f['unidad']} (Est. ${f['costo_estimado_compra']:.2f})\n"
            
            st.download_button("📥 Descargar Lista (.txt)", data=txt_compras, file_name="lista_compras.txt")

# ==========================================
# 5. VENTAS Y FINANZAS
# ==========================================
with tabs[4]:
    df_tandas = cargar_tabla("tandas")
    df_finanzas = cargar_tabla("finanzas")
    df_recetas = cargar_tabla("recetas")
    
    col_v, col_f = st.columns([1, 1])
    
    with col_v:
        st.subheader("🛒 Punto de Venta")
        if not df_tandas.empty and "stock_disponible" in df_tandas.columns:
            df_disp = df_tandas[pd.to_numeric(df_tandas["stock_disponible"], errors='coerce').fillna(0) > 0]
            
            if not df_disp.empty:
                tanda_vender_id = st.selectbox("Selecciona Lote a Vender", df_disp["id"].tolist(), format_func=lambda x: f"Lote #{x} - {df_disp[df_disp['id']==x].iloc[0]['receta_nombre']} (Disp: {df_disp[df_disp['id']==x].iloc[0]['stock_disponible']})")
                tanda_row = df_disp[df_disp["id"] == tanda_vender_id].iloc[0]
                max_cant = int(float(tanda_row["stock_disponible"]))
                
                cant_vender = st.number_input(f"Cantidad a vender ({tanda_row['receta_nombre']})", min_value=1, max_value=max_cant, step=1)
                
                if st.button("💰 Registrar Venta", type="primary"):
                    precio_v = 0.0
                    if not df_recetas.empty and tanda_row["receta_nombre"] in df_recetas["nombre"].values:
                        precio_v = float(df_recetas[df_recetas["nombre"] == tanda_row["receta_nombre"]].iloc[0]["precio_venta"])
                    
                    ingreso_total = cant_vender * precio_v
                    
                    idx_t = df_tandas[df_tandas["id"] == tanda_vender_id].index[0]
                    df_tandas.loc[idx_t, "stock_disponible"] = float(df_tandas.loc[idx_t, "stock_disponible"]) - cant_vender
                    actualizar_tabla("tandas", df_tandas)
                    
                    nuevo_id_f = obtener_nuevo_id(df_finanzas)
                    fecha_f = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    escribir_fila("finanzas", [nuevo_id_f, "Ingreso", ingreso_total, fecha_f, f"Venta de {cant_vender} {tanda_row['receta_nombre']} (Lote #{tanda_vender_id})"])
                    
                    st.success(f"Venta registrada. +${ingreso_total:.2f}")
                    st.rerun()
            else:
                st.info("No hay postres/tandas disponibles para venta.")
                
        st.write("---")
        st.subheader("➕ Registro Manual de Ingreso / Egreso")
        with st.form("form_finanza_manual"):
            tipo_m = st.selectbox("Tipo Movimiento", ["Ingreso", "Egreso"])
            monto_m = st.number_input("Monto ($)", min_value=0.0, format="%.2f")
            desc_m = st.text_input("Descripción / Motivo")
            
            if st.form_submit_button("Guardar Movimiento"):
                if monto_m > 0 and desc_m:
                    n_id = obtener_nuevo_id(df_finanzas)
                    escribir_fila("finanzas", [n_id, tipo_m, monto_m, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), desc_m])
                    st.success("Movimiento registrado.")
                    st.rerun()

    with col_f:
        st.subheader("📈 Balance Financiero")
        if not df_finanzas.empty:
            df_finanzas['monto'] = pd.to_numeric(df_finanzas['monto'], errors='coerce').fillna(0)
            ingresos = df_finanzas[df_finanzas['tipo'] == 'Ingreso']['monto'].sum()
            egresos = df_finanzas[df_finanzas['tipo'] == 'Egreso']['monto'].sum()
            balance = ingresos - egresos
            
            cm1, cm2, cm3 = st.columns(3)
            cm1.metric("Ingresos", f"${ingresos:,.2f}")
            cm2.metric("Egresos", f"${egresos:,.2f}")
            cm3.metric("Balance Neto", f"${balance:,.2f}")
            
            st.dataframe(df_finanzas, use_container_width=True, height=220)
            
            st.markdown("#### ✏️ Modificar / Eliminar Transacción")
            trans_id = st.selectbox("Selecciona Transacción ID", df_finanzas["id"].tolist() if "id" in df_finanzas.columns else [])
            if trans_id:
                f_trans = df_finanzas[df_finanzas["id"] == trans_id].iloc[0]
                with st.form("form_edit_finanza"):
                    ef_tipo = st.selectbox("Tipo", ["Ingreso", "Egreso"], index=0 if f_trans["tipo"] == "Ingreso" else 1)
                    ef_monto = st.number_input("Monto ($)", min_value=0.0, value=float(f_trans["monto"]))
                    ef_desc = st.text_input("Descripción", value=str(f_trans["descripcion"]))
                    
                    col_fb1, col_fb2 = st.columns(2)
                    if col_fb1.form_submit_button("💾 Actualizar"):
                        idx_f = df_finanzas[df_finanzas["id"] == trans_id].index[0]
                        df_finanzas.loc[idx_f, "tipo"] = ef_tipo
                        df_finanzas.loc[idx_f, "monto"] = ef_monto
                        df_finanzas.loc[idx_f, "descripcion"] = ef_desc
                        actualizar_tabla("finanzas", df_finanzas)
                        st.success("Transacción actualizada.")
                        st.rerun()
                        
                    if col_fb2.form_submit_button("🗑️ Eliminar"):
                        df_f_nueva = df_finanzas[df_finanzas["id"] != trans_id]
                        actualizar_tabla("finanzas", df_f_nueva)
                        st.warning("Transacción eliminada.")
                        st.rerun()

# ==========================================
# 6. MERMAS Y PÉRDIDAS
# ==========================================
with tabs[5]:
    df_mermas = cargar_tabla("mermas")
    df_insumos = cargar_tabla("insumos")
    df_tandas = cargar_tabla("tandas")
    
    st.subheader("🗑️ Registro de Mermas y Pérdidas")
    tipo_merma = st.radio("Tipo de pérdida:", ["Insumo (Materia prima)", "Producto Terminado (Pays / Postres)"], horizontal=True)
    
    if tipo_merma == "Insumo (Materia prima)":
        if not df_insumos.empty:
            with st.form("form_merma_insumo"):
                ins_m_sel = st.selectbox("Insumo dañado", df_insumos["nombre"].tolist())
                ins_row = df_insumos[df_insumos["nombre"] == ins_m_sel].iloc[0]
                cant_p = st.number_input(f"Cantidad perdida ({ins_row['unidad']})", min_value=0.0, max_value=float(ins_row['stock']), step=0.1)
                motivo_p = st.text_input("Motivo de la pérdida")
                
                if st.form_submit_button("Registrar Merma de Insumo", type="primary"):
                    if cant_p > 0:
                        idx_i = df_insumos[df_insumos["nombre"] == ins_m_sel].index[0]
                        df_insumos.loc[idx_i, "stock"] = float(df_insumos.loc[idx_i, "stock"]) - cant_p
                        actualizar_tabla("insumos", df_insumos)
                        
                        costo_p = cant_p * float(ins_row["costo_unidad"])
                        m_id = obtener_nuevo_id(df_mermas)
                        escribir_fila("mermas", [m_id, "Insumo", ins_m_sel, cant_p, ins_row['unidad'], costo_p, datetime.now().strftime("%Y-%m-%d %H:%M"), motivo_p])
                        st.success("Merma registrada.")
                        st.rerun()
    else:
        if not df_tandas.empty:
            df_disp_m = df_tandas[pd.to_numeric(df_tandas["stock_disponible"], errors='coerce').fillna(0) > 0]
            if not df_disp_m.empty:
                with st.form("form_merma_producto"):
                    tanda_m_id = st.selectbox("Lote afectado", df_disp_m["id"].tolist(), format_func=lambda x: f"Lote #{x} - {df_disp_m[df_disp_m['id']==x].iloc[0]['receta_nombre']}")
                    tanda_m_row = df_disp_m[df_disp_m["id"] == tanda_m_id].iloc[0]
                    
                    cant_prod_p = st.number_input("Cantidad dañada (Piezas)", min_value=1, max_value=int(float(tanda_m_row["stock_disponible"])), step=1)
                    motivo_prod_p = st.text_input("Motivo de la pérdida")
                    
                    if st.form_submit_button("Registrar Merma de Producto", type="primary"):
                        idx_t = df_tandas[df_tandas["id"] == tanda_m_id].index[0]
                        df_tandas.loc[idx_t, "stock_disponible"] = float(df_tandas.loc[idx_t, "stock_disponible"]) - cant_prod_p
                        actualizar_tabla("tandas", df_tandas)
                        
                        costo_un = float(tanda_m_row["costo_total"]) / float(tanda_m_row["cantidad_producida"]) if float(tanda_m_row["cantidad_producida"]) > 0 else 0
                        m_id = obtener_nuevo_id(df_mermas)
                        escribir_fila("mermas", [m_id, "Producto Terminado", tanda_m_row["receta_nombre"], cant_prod_p, "piezas", cant_prod_p * costo_un, datetime.now().strftime("%Y-%m-%d %H:%M"), motivo_prod_p])
                        st.success("Merma de producto registrada.")
                        st.rerun()

    st.write("---")
    st.subheader("📜 Historial y Modificación de Mermas")
    if not df_mermas.empty:
        st.dataframe(df_mermas, use_container_width=True)
        
        merma_edit_id = st.selectbox("Selecciona Merma ID para modificar/eliminar", df_mermas["id"].tolist() if "id" in df_mermas.columns else [])
        if merma_edit_id:
            m_row = df_mermas[df_mermas["id"] == merma_edit_id].iloc[0]
            with st.form("form_edit_merma"):
                em_cant = st.number_input("Cantidad", min_value=0.0, value=float(m_row["cantidad"]))
                em_costo = st.number_input("Costo Estimado ($)", min_value=0.0, value=float(m_row["costo_estimado"]))
                em_motivo = st.text_input("Motivo", value=str(m_row["motivo"]))
                
                col_mb1, col_mb2 = st.columns(2)
                if col_mb1.form_submit_button("💾 Actualizar Merma"):
                    idx_m = df_mermas[df_mermas["id"] == merma_edit_id].index[0]
                    df_mermas.loc[idx_m, "cantidad"] = em_cant
                    df_mermas.loc[idx_m, "costo_estimado"] = em_costo
                    df_mermas.loc[idx_m, "motivo"] = em_motivo
                    actualizar_tabla("mermas", df_mermas)
                    st.success("Merma actualizada.")
                    st.rerun()
                    
                if col_mb2.form_submit_button("🗑️ Eliminar Merma"):
                    df_m_nueva = df_mermas[df_mermas["id"] != merma_edit_id]
                    actualizar_tabla("mermas", df_m_nueva)
                    st.warning("Merma eliminada.")
                    st.rerun()

# ==========================================
# 7. RESPALDOS Y AJUSTES
# ==========================================
with tabs[6]:
    st.subheader("⚙️ Copia de Seguridad y Mantenimiento")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("#### 💾 Exportar Copia de Seguridad")
        if st.button("Generar Respaldo JSON"):
            datos = {
                "insumos": cargar_tabla("insumos").to_dict(orient="records"),
                "recetas": cargar_tabla("recetas").to_dict(orient="records"),
                "receta_ingredientes": cargar_tabla("receta_ingredientes").to_dict(orient="records"),
                "tandas": cargar_tabla("tandas").to_dict(orient="records"),
                "finanzas": cargar_tabla("finanzas").to_dict(orient="records"),
                "mermas": cargar_tabla("mermas").to_dict(orient="records")
            }
            st.download_button("⬇️ Descargar Archivo", data=json.dumps(datos, indent=4), file_name="respaldo_ladypays.json", mime="application/json")
            
    with col_b2:
        st.markdown("#### ⚠️ Restablecer Datos Iniciales")
        confirmacion = st.text_input("Escribe 'BORRAR' para reiniciar el sistema")
        if st.button("Formatear Sistema", type="primary") and confirmacion == "BORRAR":
            actualizar_tabla("insumos", pd.DataFrame(columns=["nombre", "categoria", "unidad", "costo_unidad", "stock", "stock_minimo"]))
            actualizar_tabla("recetas", pd.DataFrame(columns=["id", "nombre", "categoria", "precio_venta"]))
            actualizar_tabla("receta_ingredientes", pd.DataFrame(columns=["receta_id", "insumo_nombre", "cantidad"]))
            actualizar_tabla("tandas", pd.DataFrame(columns=["id", "receta_nombre", "cantidad_producida", "stock_disponible", "costo_total", "fecha", "notas"]))
            actualizar_tabla("finanzas", pd.DataFrame(columns=["id", "tipo", "monto", "fecha", "descripcion"]))
            actualizar_tabla("mermas", pd.DataFrame(columns=["id", "tipo", "nombre", "cantidad", "unidad", "costo_estimado", "fecha", "motivo"]))
            st.success("Sistema restablecido de fábrica.")
            st.rerun()
