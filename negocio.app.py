# -*- coding: utf-8 -*-
"""
💎 Lady Pays – Control de Insumos, Recetas, Producción y Finanzas
Versión Optimizada para Móvil y Uso Individual (Con automatización de gastos).
"""
import json
from datetime import datetime, timedelta, timezone

import gspread
import pandas as pd
import streamlit as st

# ==========================================
# CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ==========================================
st.set_page_config(page_title="Lady Pays", layout="wide", page_icon="💎", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #fafafa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border: 1px solid #e0e0e0; box-shadow: 1px 1px 5px rgba(0,0,0,0.05); }
    div[data-testid="stExpander"] { background-color: #ffffff; border-radius: 8px; }
    button[kind="primary"] { font-weight: 600; width: 100%; margin-top: 10px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# UTILIDADES GENERALES
# ==========================================
try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Mexico_City")
except Exception:
    ZONA = timezone(timedelta(hours=-6))

def ahora(): return datetime.now(ZONA)
def ahora_str(): return ahora().strftime("%Y-%m-%d %H:%M:%S")
def dinero(x):
    try: return f"${float(x):,.2f}"
    except Exception: return "$0.00"

def _version_streamlit():
    try: return tuple(int(p) for p in st.__version__.split(".")[:2])
    except Exception: return (0, 0)

_ANCHO = {"width": "stretch"} if _version_streamlit() >= (1, 50) else {"use_container_width": True}

def mostrar_df(df, **kw): st.dataframe(df, **_ANCHO, **kw)
def editor_df(df, **kw): return st.data_editor(df, **_ANCHO, **kw)

def aviso(msg, tipo="success"):
    st.session_state["_aviso"] = (tipo, msg)

def mostrar_aviso():
    a = st.session_state.pop("_aviso", None)
    if a:
        tipo, msg = a
        {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}[tipo](msg)

def _py(v):
    if hasattr(v, "item"):
        try: return v.item()
        except Exception: pass
    if isinstance(v, float) and v != v: return ""
    return v

# ==========================================
# ACCESO OPCIONAL CON CONTRASEÑA
# ==========================================
def verificar_acceso():
    try: clave = st.secrets.get("app_password", "")
    except Exception: clave = ""
    if not clave or st.session_state.get("acceso_ok"): return
    
    st.title("💎 Lady Pays")
    intento = st.text_input("Contraseña", type="password")
    if st.button("Entrar", type="primary"):
        if intento == str(clave):
            st.session_state["acceso_ok"] = True
            st.rerun()
        else: st.error("Contraseña incorrecta.")
    st.stop()

verificar_acceso()

# ==========================================
# CONEXIÓN A GOOGLE SHEETS
# ==========================================
@st.cache_resource(show_spinner="Conectando...")
def get_sheets_connection():
    bruto = st.secrets["google_credentials"]
    secretos = json.loads(bruto, strict=False) if isinstance(bruto, str) else dict(bruto)
    try:
        cliente = gspread.service_account_from_dict(secretos)
    except AttributeError:
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(secretos, scope)
        cliente = gspread.authorize(creds)
    return cliente.open("Inventario_Negocio")

try: sh = get_sheets_connection()
except Exception as e:
    st.error("❌ Error de conexión con Google Sheets.")
    st.stop()

ESQUEMA = {
    "insumos": {"nombre": "t", "categoria": "t", "unidad": "t", "costo_unidad": "n", "stock": "n", "stock_minimo": "n"},
    "recetas": {"id": "i", "nombre": "t", "categoria": "t", "precio_venta": "n"},
    "receta_ingredientes": {"receta_id": "i", "insumo_nombre": "t", "cantidad": "n"},
    "tandas": {"id": "i", "receta_nombre": "t", "cantidad_producida": "n", "stock_disponible": "n", "costo_total": "n", "fecha": "t", "notas": "t"},
    "finanzas": {"id": "i", "tipo": "t", "monto": "n", "fecha": "t", "descripcion": "t"},
    "mermas": {"id": "i", "tipo": "t", "nombre": "t", "cantidad": "n", "unidad": "t", "costo_estimado": "n", "fecha": "t", "motivo": "t"},
}

@st.cache_data(ttl=300, show_spinner=False)
def _leer_tabla(nombre):
    try: ws = sh.worksheet(nombre)
    except gspread.WorksheetNotFound: return None
    return pd.DataFrame(ws.get_all_records())

def _normalizar(df, nombre):
    esquema = ESQUEMA[nombre]
    columnas = list(esquema)
    if df is None or df.empty: return pd.DataFrame(columns=columnas)
    df = df.copy()
    for c in columnas:
        if c not in df.columns: df[c] = ""
    vacias = df[columnas].astype(str).apply(lambda s: s.str.strip()).eq("").all(axis=1)
    df = df[~vacias].copy()
    for c, tipo in esquema.items():
        if tipo == "t": df[c] = df[c].fillna("").astype(str).str.strip()
        elif tipo == "n": df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype(float)
        else: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df.reset_index(drop=True)

def cargar_tabla(nombre):
    try: crudo = _leer_tabla(nombre)
    except Exception as e:
        st.error(f"⚠️ Error al leer «{nombre}». Espera y presiona Sincronizar.")
        st.stop()
    return _normalizar(crudo, nombre)

def recargar_cache(): st.cache_data.clear()

def escribir_filas(nombre, filas):
    if not filas: return
    try:
        limpias = [[_py(v) for v in fila] for fila in filas]
        sh.worksheet(nombre).append_rows(limpias, value_input_option="RAW")
        recargar_cache()
    except Exception as e:
        st.error(f"Error escribiendo en {nombre}: {e}")
        st.stop()

def escribir_fila(nombre, fila): escribir_filas(nombre, [fila])

def actualizar_tabla(nombre, df):
    try:
        ws = sh.worksheet(nombre)
        columnas = list(df.columns)
        valores = [columnas] + [[_py(v) for v in fila] for fila in df.fillna("").values.tolist()]
        filas_previas = ws.row_count
        if len(valores) > ws.row_count: ws.resize(rows=len(valores) + 100)
        if len(columnas) > ws.col_count: ws.resize(cols=len(columnas))
        ws.update(values=valores, range_name="A1")
        if filas_previas > len(valores): ws.batch_clear([f"A{len(valores) + 1}:Z{filas_previas}"])
        recargar_cache()
    except Exception as e:
        st.error(f"Error al actualizar {nombre}: {e}")
        st.stop()

def obtener_nuevo_id(df):
    if df.empty or 'id' not in df.columns: return 1
    try: return int(pd.to_numeric(df['id'], errors='coerce').max()) + 1
    except Exception: return 1

@st.cache_resource
def inicializar_base_datos():
    try: ws_existentes = {ws.title: ws for ws in sh.worksheets()}
    except Exception: return False
    for nombre, esquema in ESQUEMA.items():
        columnas = list(esquema)
        if nombre not in ws_existentes:
            ws = sh.add_worksheet(title=nombre, rows="100", cols="20")
            ws.append_row(columnas)
        else:
            ws = ws_existentes[nombre]
            if not ws.row_values(1): ws.append_row(columnas)
    return True

inicializar_base_datos()

CATEGORIAS_BASE = ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"]
UNIDADES_BASE = ["latas", "paquetes", "g", "ml", "piezas", "Kg", "Litro"]

def opciones(base, existentes):
    extra = [x for x in existentes if x and x not in base]
    return list(base) + sorted(set(extra))

def detalle_receta(receta_id, df_ri, df_insumos):
    filas, total, faltan = [], 0.0, []
    for _, ing in df_ri[df_ri["receta_id"] == receta_id].iterrows():
        d = df_insumos[df_insumos["nombre"] == ing["insumo_nombre"]]
        cant = float(ing["cantidad"])
        if d.empty:
            faltan.append(ing["insumo_nombre"])
            costo_u, unidad = 0.0, "—"
        else:
            costo_u, unidad = float(d.iloc[0]["costo_unidad"]), d.iloc[0]["unidad"]
        costo = costo_u * cant
        total += costo
        filas.append({"Ingrediente": ing["insumo_nombre"], "Cantidad": cant, "Unidad": unidad, "Costo": dinero(costo)})
    return filas, total, faltan

# ==========================================
# MENÚ LATERAL (MÓVIL FRIENDLY)
# ==========================================
with st.sidebar:
    st.title("💎 Lady Pays")
    if st.button("🔄 Sincronizar Nube"):
        recargar_cache()
        st.rerun()
    
    st.markdown("---")
    menu = st.radio("Navegación", [
        "🏠 Panel Principal",
        "📦 Inventario de Insumos",
        "📖 Recetas",
        "💰 Finanzas", # NUEVA SECCIÓN
        "🛒 Lista de Compras",
        "🗑️ Mermas",
        "⚙️ Ajustes y Respaldos"
    ])

mostrar_aviso()

# ==========================================
# 0. PANEL PRINCIPAL (ACCIONES RÁPIDAS)
# ==========================================
if menu == "🏠 Panel Principal":
    _ins_r, _tan_r, _fin_r = cargar_tabla("insumos"), cargar_tabla("tandas"), cargar_tabla("finanzas")
    _df_recetas = cargar_tabla("recetas")
    _mes, _hoy = ahora().strftime("%Y-%m"), ahora().strftime("%Y-%m-%d")
    
    # Cálculos para el panel principal
    _ingresos_hoy = _fin_r[(_fin_r["tipo"] == "Ingreso") & (_fin_r["fecha"].str.startswith(_hoy))]["monto"].sum()
    _ingresos_mes = _fin_r[(_fin_r["tipo"] == "Ingreso") & (_fin_r["fecha"].str.startswith(_mes))]["monto"].sum()
    _crit_r = int((_ins_r["stock"] <= _ins_r["stock_minimo"]).sum()) if not _ins_r.empty else 0

    col1, col2 = st.columns(2)
    col1.metric("Ventas de hoy", dinero(_ingresos_hoy))
    col2.metric("Ingresos del mes", dinero(_ingresos_mes))
    col3, col4 = st.columns(2)
    col3.metric("Pays listos", f"{_tan_r['stock_disponible'].sum():g}")
    col4.metric("Insumos críticos", _crit_r)

    st.write("---")
    
    # ACCIÓN RÁPIDA: VENDER
    st.subheader("💰 Registrar Venta Rápida")
    df_disp = _tan_r[_tan_r["stock_disponible"] >= 1]
    if not df_disp.empty:
        etiquetas_v = {int(r.id): f"Lote #{r.id} - {r.receta_nombre} (Disp: {r.stock_disponible:g})" for r in df_disp.itertuples()}
        tanda_vender_id = st.selectbox("Selecciona Lote", list(etiquetas_v), format_func=lambda x: etiquetas_v[x])
        tanda_row = df_disp[df_disp["id"] == tanda_vender_id].iloc[0]
        
        precio_def = 0.0
        rec_p = _df_recetas[_df_recetas["nombre"] == tanda_row["receta_nombre"]]
        if not rec_p.empty: precio_def = float(rec_p.iloc[0]["precio_venta"])
        
        c1, c2 = st.columns(2)
        cant_v = c1.number_input("Cantidad", min_value=1, max_value=int(tanda_row["stock_disponible"]), value=1)
        precio_v = c2.number_input("Precio c/u ($)", min_value=0.0, value=precio_def, format="%.2f")
        nota = st.text_input("Nota (opcional)")
        
        if st.button("Registrar Venta", type="primary"):
            ingreso_total = cant_v * precio_v
            desc = f"Venta de {cant_v} {tanda_row['receta_nombre']} (Lote #{tanda_vender_id})"
            if nota: desc += f" - {nota}"
            nuevo_id_f = obtener_nuevo_id(_fin_r)
            escribir_fila("finanzas", [nuevo_id_f, "Ingreso", ingreso_total, ahora_str(), desc])
            
            idx_t = _tan_r[_tan_r["id"] == tanda_vender_id].index[0]
            _tan_r.loc[idx_t, "stock_disponible"] = float(_tan_r.loc[idx_t, "stock_disponible"]) - cant_v
            actualizar_tabla("tandas", _tan_r)
            aviso(f"✅ Venta por {dinero(ingreso_total)} registrada.")
            st.rerun()
    else:
        st.info("No hay pays disponibles. Registra producción primero.")

    st.write("---")

    # ACCIÓN RÁPIDA: PRODUCIR
    st.subheader("🍳 Registrar Producción")
    if not _df_recetas.empty:
        df_ri = cargar_tabla("receta_ingredientes")
        rec_sel = st.selectbox("Receta a producir", _df_recetas["nombre"].tolist())
        cant_prep = st.number_input("Número de Tandas", min_value=1, step=1, value=1)
        
        receta_row = _df_recetas[_df_recetas["nombre"] == rec_sel].iloc[0]
        ingredientes = df_ri[df_ri["receta_id"] == receta_row["id"]]
        
        if st.button("Producir y Descontar Insumos"):
            suficiente = True
            costo_total = 0.0
            for _, row in ingredientes.iterrows():
                cant_nec = float(row["cantidad"]) * cant_prep
                ins_data = _ins_r[_ins_r["nombre"] == row["insumo_nombre"]]
                if ins_data.empty or float(ins_data.iloc[0]['stock']) < cant_nec:
                    st.error(f"❌ Falta {row['insumo_nombre']} en inventario.")
                    suficiente = False
                    break
                costo_total += cant_nec * float(ins_data.iloc[0]['costo_unidad'])
            
            if suficiente and not ingredientes.empty:
                for _, row in ingredientes.iterrows():
                    mask = _ins_r["nombre"] == row["insumo_nombre"]
                    _ins_r.loc[mask, "stock"] = _ins_r.loc[mask, "stock"].astype(float) - (float(row["cantidad"]) * cant_prep)
                actualizar_tabla("insumos", _ins_r)
                
                n_id = obtener_nuevo_id(_tan_r)
                escribir_fila("tandas", [n_id, rec_sel, int(cant_prep), int(cant_prep), costo_total, ahora_str(), ""])
                aviso(f"✅ Producción de {rec_sel} registrada.")
                st.rerun()
    else:
        st.info("No hay recetas registradas.")

# ==========================================
# 1. INVENTARIO DE INSUMOS (CON GASTOS AUTOMÁTICOS)
# ==========================================
elif menu == "📦 Inventario de Insumos":
    df_insumos = cargar_tabla("insumos")
    st.title("Inventario de Insumos")
    
    cats_op = opciones(CATEGORIAS_BASE, df_insumos["categoria"].tolist() if not df_insumos.empty else [])
    unis_op = opciones(UNIDADES_BASE, df_insumos["unidad"].tolist() if not df_insumos.empty else [])

    if not df_insumos.empty:
        st.markdown("### Tus Insumos")
        mostrar_df(df_insumos[["nombre", "stock", "unidad", "stock_minimo", "costo_unidad"]], hide_index=True)

    with st.expander("➕ Agregar Insumo Nuevo o Reabastecer"):
        modo = st.radio("Acción", ["Reabastecer existente", "Insumo Nuevo"], horizontal=True)
        
        if modo == "Insumo Nuevo":
            with st.form("form_nuevo_insumo", clear_on_submit=True):
                nombre = st.text_input("Nombre")
                col1, col2 = st.columns(2)
                categoria = col1.selectbox("Categoría", cats_op)
                unidad = col2.selectbox("Unidad", unis_op)
                costo = col1.number_input("Costo Unitario ($)", min_value=0.0)
                cantidad = col2.number_input("Cantidad inicial", min_value=0.0)
                stock_min = st.number_input("Stock mínimo", min_value=0.0)
                
                if st.form_submit_button("Guardar Insumo"):
                    if nombre.strip():
                        escribir_fila("insumos", [nombre.strip(), categoria, unidad, costo, cantidad, stock_min])
                        
                        # AUTOMATIZACIÓN DE GASTO: Si hubo cantidad inicial, se registra como egreso.
                        if cantidad > 0 and costo > 0:
                            df_fin = cargar_tabla("finanzas")
                            id_fin = obtener_nuevo_id(df_fin)
                            gasto_total = cantidad * costo
                            escribir_fila("finanzas", [id_fin, "Egreso", gasto_total, ahora_str(), f"Compra inicial de: {nombre.strip()} ({cantidad} {unidad})"])
                        
                        aviso("✅ Insumo guardado e inventario actualizado.")
                        st.rerun()
        else:
            if not df_insumos.empty:
                ins_re = st.selectbox("Insumo", df_insumos["nombre"].tolist())
                with st.form("form_reab"):
                    cant_re = st.number_input("Cantidad comprada", min_value=0.0)
                    if st.form_submit_button("Sumar al inventario"):
                        idx = df_insumos[df_insumos["nombre"] == ins_re].index[0]
                        costo_u = float(df_insumos.loc[idx, "costo_unidad"])
                        unidad_ins = df_insumos.loc[idx, "unidad"]
                        
                        df_insumos.loc[idx, "stock"] = float(df_insumos.loc[idx, "stock"]) + cant_re
                        actualizar_tabla("insumos", df_insumos)
                        
                        # AUTOMATIZACIÓN DE GASTO: Registra el costo de la compra de inmediato.
                        if cant_re > 0 and costo_u > 0:
                            df_fin = cargar_tabla("finanzas")
                            id_fin = obtener_nuevo_id(df_fin)
                            gasto_total = cant_re * costo_u
                            escribir_fila("finanzas", [id_fin, "Egreso", gasto_total, ahora_str(), f"Reabastecimiento de: {ins_re} ({cant_re} {unidad_ins})"])
                        
                        aviso("✅ Inventario y finanzas actualizados.")
                        st.rerun()

# ==========================================
# 2. RECETAS
# ==========================================
elif menu == "📖 Recetas":
    st.title("Gestión de Recetas")
    df_recetas = cargar_tabla("recetas")
    df_ri = cargar_tabla("receta_ingredientes")
    df_insumos = cargar_tabla("insumos")

    if not df_recetas.empty:
        for _, r in df_recetas.iterrows():
            with st.expander(f"🍰 {r['nombre']} - Precio: {dinero(r['precio_venta'])}"):
                filas, costo, faltan = detalle_receta(r["id"], df_ri, df_insumos)
                if filas: st.table(pd.DataFrame(filas))
                st.write(f"**Costo estimado:** {dinero(costo)}")

    with st.expander("➕ Crear Nueva Receta"):
        if 'ing_temp' not in st.session_state: st.session_state.ing_temp = []
        nom_rec = st.text_input("Nombre de receta")
        p_venta = st.number_input("Precio de Venta ($)", min_value=0.0)
        
        lista_ins = df_insumos["nombre"].tolist() if not df_insumos.empty else []
        if lista_ins:
            ins_sel = st.selectbox("Agregar Insumo", lista_ins)
            cant_sel = st.number_input("Cantidad a usar", min_value=0.0)
            if st.button("Añadir Insumo"):
                st.session_state.ing_temp.append({"insumo_nombre": ins_sel, "cantidad": cant_sel})
                st.rerun()
                
            if st.session_state.ing_temp:
                st.write("Ingredientes actuales:")
                st.json(st.session_state.ing_temp)
                if st.button("💾 Guardar Receta Completa", type="primary"):
                    n_id = obtener_nuevo_id(df_recetas)
                    escribir_fila("recetas", [n_id, nom_rec, "PAYS", p_venta])
                    escribir_filas("receta_ingredientes", [[n_id, ing['insumo_nombre'], ing['cantidad']] for ing in st.session_state.ing_temp])
                    st.session_state.ing_temp = []
                    aviso("✅ Receta creada.")
                    st.rerun()

# ==========================================
# 3. FINANZAS (NUEVA SECCIÓN)
# ==========================================
elif menu == "💰 Finanzas":
    st.title("Flujo de Caja y Finanzas")
    df_fin = cargar_tabla("finanzas")
    
    if not df_fin.empty:
        mes_actual = ahora().strftime("%Y-%m")
        df_mes = df_fin[df_fin['fecha'].str.startswith(mes_actual)]
        
        ingresos = df_mes[df_mes['tipo'] == "Ingreso"]['monto'].sum()
        egresos = df_mes[df_mes['tipo'] == "Egreso"]['monto'].sum()
        utilidad = ingresos - egresos
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Ingresos (Mes)", dinero(ingresos))
        c2.metric("Gastos Insumos (Mes)", dinero(egresos))
        c3.metric("UTILIDAD NETA (Mes)", dinero(utilidad))
        
        st.markdown("---")
        st.subheader("Historial de Movimientos")
        
        # Invertimos el orden para ver lo más nuevo hasta arriba
        df_mostrar = df_fin.sort_values(by="fecha", ascending=False).copy()
        
        # Damos formato de dinero a la columna de monto para que se vea mejor
        df_mostrar['monto'] = df_mostrar['monto'].apply(dinero)
        mostrar_df(df_mostrar[['fecha', 'tipo', 'monto', 'descripcion']], hide_index=True)
    else:
        st.info("Aún no hay movimientos financieros registrados.")

# ==========================================
# 4. LISTA DE COMPRAS
# ==========================================
elif menu == "🛒 Lista de Compras":
    st.title("Lista de Compras")
    df_insumos = cargar_tabla("insumos")
    if not df_insumos.empty:
        faltantes = df_insumos[df_insumos['stock'] <= df_insumos['stock_minimo']].copy()
        if faltantes.empty:
            st.success("✔️️ ¡Inventario completo!")
        else:
            faltantes['sugerido'] = (faltantes['stock_minimo'] - faltantes['stock']).clip(lower=1)
            mostrar_df(faltantes[['nombre', 'stock', 'stock_minimo', 'sugerido', 'unidad']], hide_index=True)

# ==========================================
# 5. MERMAS
# ==========================================
elif menu == "🗑️ Mermas":
    st.title("Registro de Mermas")
    df_insumos = cargar_tabla("insumos")
    df_mermas = cargar_tabla("mermas")
    
    with st.form("merma_form"):
        ins_sel = st.selectbox("Insumo dañado", df_insumos["nombre"].tolist() if not df_insumos.empty else [])
        cant = st.number_input("Cantidad perdida", min_value=0.0)
        motivo = st.text_input("Motivo")
        if st.form_submit_button("Registrar Merma", type="primary"):
            if cant > 0:
                idx_i = df_insumos[df_insumos["nombre"] == ins_sel].index[0]
                df_insumos.loc[idx_i, "stock"] = float(df_insumos.loc[idx_i, "stock"]) - cant
                actualizar_tabla("insumos", df_insumos)
                m_id = obtener_nuevo_id(df_mermas)
                escribir_fila("mermas", [m_id, "Insumo", ins_sel, cant, "unidad", 0, ahora_str(), motivo])
                aviso("✅ Merma registrada e inventario descontado.")
                st.rerun()

# ==========================================
# 6. AJUSTES Y RESPALDOS
# ==========================================
elif menu == "⚙️ Ajustes y Respaldos":
    st.title("Ajustes del Sistema")
    st.write("Descarga una copia de seguridad de todos tus datos.")
    datos = {nombre: cargar_tabla(nombre).to_dict(orient="records") for nombre in ESQUEMA}
    st.download_button("⬇️ Descargar Respaldo (JSON)",
                       data=json.dumps(datos, indent=4, ensure_ascii=False, default=str),
                       file_name=f"respaldo_ladypays_{ahora().strftime('%Y%m%d')}.json",
                       mime="application/json")
