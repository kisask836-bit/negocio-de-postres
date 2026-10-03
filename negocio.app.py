# -*- coding: utf-8 -*-
"""
💎 Lady Pays – Control de Insumos, Recetas, Producción y Finanzas
Versión revisada: más segura con los datos, más amigable y con menos errores.

Secrets requeridos (.streamlit/secrets.toml o panel de Streamlit Cloud):
    google_credentials = "<JSON de la cuenta de servicio>"
Secret opcional:
    app_password = "tu_contraseña"      # si lo defines, la app pedirá clave
"""
import json
from datetime import datetime, timedelta, timezone

import gspread
import pandas as pd
import streamlit as st

# ==========================================
# CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ==========================================
st.set_page_config(page_title="Lady Pays", layout="wide", page_icon="💎")

st.markdown("""
<style>
    .stApp { background-color: #fafafa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border: 1px solid #e0e0e0; }
    div[data-testid="stExpander"] { background-color: #ffffff; border-radius: 8px; }
    button[kind="primary"] { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# UTILIDADES GENERALES
# ==========================================
# Zona horaria de México (Streamlit Cloud trabaja en UTC; sin esto las ventas
# de la noche se guardarían con fecha del día siguiente).
try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Mexico_City")
except Exception:
    ZONA = timezone(timedelta(hours=-6))


def ahora():
    return datetime.now(ZONA)


def ahora_str():
    return ahora().strftime("%Y-%m-%d %H:%M:%S")


def dinero(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "$0.00"


def _version_streamlit():
    try:
        return tuple(int(p) for p in st.__version__.split(".")[:2])
    except Exception:
        return (0, 0)


# Compatibilidad: en Streamlit nuevo "use_container_width" quedó obsoleto.
_ANCHO = {"width": "stretch"} if _version_streamlit() >= (1, 50) else {"use_container_width": True}


def mostrar_df(df, **kw):
    st.dataframe(df, **_ANCHO, **kw)


def editor_df(df, **kw):
    return st.data_editor(df, **_ANCHO, **kw)


def aviso(msg, tipo="success"):
    """Guarda un mensaje para mostrarlo DESPUÉS del st.rerun() (antes se perdía al instante)."""
    st.session_state["_aviso"] = (tipo, msg)


def mostrar_aviso():
    a = st.session_state.pop("_aviso", None)
    if a:
        tipo, msg = a
        {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}[tipo](msg)


def _py(v):
    """Convierte tipos de numpy/pandas a tipos simples que Google Sheets acepta."""
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return v


# ==========================================
# ACCESO OPCIONAL CON CONTRASEÑA
# ==========================================
def verificar_acceso():
    try:
        clave = st.secrets.get("app_password", "")
    except Exception:
        clave = ""
    if not clave or st.session_state.get("acceso_ok"):
        return
    st.title("💎 Lady Pays")
    st.caption("Ingresa la contraseña para continuar")
    intento = st.text_input("Contraseña", type="password", key="pw_input")
    if st.button("Entrar", type="primary", key="pw_btn"):
        if intento == str(clave):
            st.session_state["acceso_ok"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    st.stop()


verificar_acceso()

# ==========================================
# CONEXIÓN A GOOGLE SHEETS
# ==========================================
@st.cache_resource(show_spinner="Conectando con Google Sheets...")
def get_sheets_connection():
    bruto = st.secrets["google_credentials"]
    secretos = json.loads(bruto, strict=False) if isinstance(bruto, str) else dict(bruto)
    try:
        cliente = gspread.service_account_from_dict(secretos)
    except AttributeError:  # gspread antiguo
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(secretos, scope)
        cliente = gspread.authorize(creds)
    return cliente.open("Inventario_Negocio")


try:
    sh = get_sheets_connection()
except Exception as e:
    st.error("❌ No se pudo conectar con Google Sheets. Revisa que el secreto `google_credentials` "
             "esté bien copiado y que la hoja **Inventario_Negocio** esté compartida con la cuenta de servicio.")
    st.code(str(e))
    st.stop()

# Estructura de la base de datos: tipo "t" = texto, "n" = número, "i" = entero (ids)
ESQUEMA = {
    "insumos": {"nombre": "t", "categoria": "t", "unidad": "t", "costo_unidad": "n", "stock": "n", "stock_minimo": "n"},
    "recetas": {"id": "i", "nombre": "t", "categoria": "t", "precio_venta": "n"},
    "receta_ingredientes": {"receta_id": "i", "insumo_nombre": "t", "cantidad": "n"},
    "tandas": {"id": "i", "receta_nombre": "t", "cantidad_producida": "n", "stock_disponible": "n",
               "costo_total": "n", "fecha": "t", "notas": "t"},
    "finanzas": {"id": "i", "tipo": "t", "monto": "n", "fecha": "t", "descripcion": "t"},
    "mermas": {"id": "i", "tipo": "t", "nombre": "t", "cantidad": "n", "unidad": "t",
               "costo_estimado": "n", "fecha": "t", "motivo": "t"},
}


@st.cache_data(ttl=300, show_spinner=False)
def _leer_tabla(nombre):
    try:
        ws = sh.worksheet(nombre)
    except gspread.WorksheetNotFound:
        return None
    return pd.DataFrame(ws.get_all_records())


def _normalizar(df, nombre):
    """Garantiza columnas y tipos correctos (evita KeyError y errores de texto/número)."""
    esquema = ESQUEMA[nombre]
    columnas = list(esquema)
    if df is None or df.empty:
        return pd.DataFrame(columns=columnas)
    df = df.copy()
    for c in columnas:
        if c not in df.columns:
            df[c] = ""
    vacias = df[columnas].astype(str).apply(lambda s: s.str.strip()).eq("").all(axis=1)
    df = df[~vacias].copy()
    for c, tipo in esquema.items():
        if tipo == "t":
            df[c] = df[c].fillna("").astype(str).str.strip()
        elif tipo == "n":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        else:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df.reset_index(drop=True)


def cargar_tabla(nombre):
    """Lee una pestaña. Si Google falla, DETIENE la app en vez de devolver una tabla
    vacía (antes, una tabla vacía por error podía borrar toda la hoja al guardar)."""
    try:
        crudo = _leer_tabla(nombre)
    except Exception as e:
        st.error(f"⚠️ No se pudo leer «{nombre}» de Google Sheets (puede ser el límite de lecturas por minuto). "
                 "Espera unos segundos y presiona **🔄 Sincronizar Datos**.")
        st.caption(str(e))
        st.stop()
    return _normalizar(crudo, nombre)


def recargar_cache():
    st.cache_data.clear()


def escribir_filas(nombre, filas):
    """Agrega varias filas en UNA sola llamada a Google."""
    if not filas:
        return
    try:
        limpias = [[_py(v) for v in fila] for fila in filas]
        sh.worksheet(nombre).append_rows(limpias, value_input_option="RAW")
        recargar_cache()
    except Exception as e:
        st.error(f"Error al escribir en {nombre}: {e}")
        st.stop()


def escribir_fila(nombre, fila):
    escribir_filas(nombre, [fila])


def actualizar_tabla(nombre, df):
    """Reescribe una pestaña. Primero ESCRIBE los datos nuevos y después limpia lo que sobra
    (antes se borraba primero: si algo fallaba a la mitad se perdía la información)."""
    try:
        ws = sh.worksheet(nombre)
        columnas = list(df.columns)
        valores = [columnas] + [[_py(v) for v in fila] for fila in df.fillna("").values.tolist()]
        filas_previas = ws.row_count
        if len(valores) > ws.row_count:
            ws.resize(rows=len(valores) + 100)
        if len(columnas) > ws.col_count:
            ws.resize(cols=len(columnas))
        ws.update(values=valores, range_name="A1")
        if filas_previas > len(valores):
            ws.batch_clear([f"A{len(valores) + 1}:Z{filas_previas}"])
        recargar_cache()
    except Exception as e:
        st.error(f"Error al actualizar {nombre}: {e}")
        st.stop()


def obtener_nuevo_id(df):
    if df.empty or 'id' not in df.columns:
        return 1
    try:
        return int(pd.to_numeric(df['id'], errors='coerce').max()) + 1
    except Exception:
        return 1


@st.cache_resource
def inicializar_base_datos():
    try:
        ws_existentes = {ws.title: ws for ws in sh.worksheets()}
    except Exception:
        return False
    for nombre, esquema in ESQUEMA.items():
        columnas = list(esquema)
        if nombre not in ws_existentes:
            ws = sh.add_worksheet(title=nombre, rows="100", cols="20")
            ws.append_row(columnas)
        else:
            ws = ws_existentes[nombre]
            if not ws.row_values(1):
                ws.append_row(columnas)
    return True


inicializar_base_datos()

CATEGORIAS_BASE = ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"]
UNIDADES_BASE = ["latas", "paquetes", "g", "ml", "piezas", "Kg", "Litro"]


def opciones(base, existentes):
    extra = [x for x in existentes if x and x not in base]
    return list(base) + sorted(set(extra))


def detalle_receta(receta_id, df_ri, df_insumos):
    """Devuelve (filas para mostrar, costo total, lista de insumos que ya no existen)."""
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
        filas.append({"Ingrediente": ing["insumo_nombre"], "Cantidad": cant, "Unidad": unidad,
                      "Costo Estimado": dinero(costo)})
    return filas, total, faltan


# ==========================================
# CABECERA Y RESUMEN
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

mostrar_aviso()

with st.expander("❓ ¿Cómo se usa? (guía rápida)"):
    st.markdown("""
1. **📦 Inventario**: registra tus insumos con su costo y cantidad. Cada compra se anota sola como *Egreso*.
2. **📖 Recetas**: arma tus recetas con los insumos que usan; verás cuánto cuesta producirlas y tu ganancia.
3. **🍳 Registrar Tanda**: cuando produzcas, el sistema descuenta los insumos del inventario.
4. **💰 Ventas/Finanzas**: vende desde tus lotes y revisa ingresos, egresos y balance.
5. **🛒 Lista de Compras**: te dice qué comprar cuando algo se está acabando.
6. **🗑️ Mermas**: registra lo que se dañó o se echó a perder.
7. **⚙️ Respaldos**: descarga una copia de seguridad de vez en cuando.

💡 *El sistema trata cada **tanda como una unidad vendible** (1 pay = 1 tanda).*
""")

_ins_r, _tan_r, _fin_r = cargar_tabla("insumos"), cargar_tabla("tandas"), cargar_tabla("finanzas")
_mes, _hoy = ahora().strftime("%Y-%m"), ahora().strftime("%Y-%m-%d")
_ing_mes = _fin_r[(_fin_r["tipo"] == "Ingreso") & (_fin_r["fecha"].str.startswith(_mes))]["monto"].sum()
_egr_mes = _fin_r[(_fin_r["tipo"] == "Egreso") & (_fin_r["fecha"].str.startswith(_mes))]["monto"].sum()
_ventas_hoy = _fin_r[(_fin_r["tipo"] == "Ingreso") & (_fin_r["fecha"].str.startswith(_hoy))]["monto"].sum()
_crit_r = int((_ins_r["stock"] <= _ins_r["stock_minimo"]).sum()) if not _ins_r.empty else 0

r1, r2, r3, r4 = st.columns(4)
r1.metric("Ventas de hoy", dinero(_ventas_hoy))
r2.metric("Balance del mes", dinero(_ing_mes - _egr_mes))
r3.metric("Piezas listas para vender", f"{_tan_r['stock_disponible'].sum():g}")
r4.metric("Insumos por agotarse", _crit_r)

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
    df_ri_all = cargar_tabla("receta_ingredientes")
    df_recetas_all = cargar_tabla("recetas")

    mask_crit = (df_insumos["stock"] <= df_insumos["stock_minimo"]) if not df_insumos.empty else pd.Series(dtype=bool)
    criticos = int(mask_crit.sum())
    valor_inv = float((df_insumos["stock"] * df_insumos["costo_unidad"]).sum()) if not df_insumos.empty else 0.0

    col_k1, col_k2, col_k3 = st.columns(3)
    col_k1.metric("Total Insumos", len(df_insumos))
    col_k2.metric("Insumos en Stock Crítico", criticos, help="Stock actual menor o igual al mínimo")
    col_k3.metric("Valor del Inventario", dinero(valor_inv))

    if criticos:
        nombres = df_insumos[mask_crit]["nombre"].tolist()
        st.warning("⚠️ Por agotarse: " + ", ".join(nombres[:8]) + (f" y {len(nombres) - 8} más…" if len(nombres) > 8 else ""))

    cats_op = opciones(CATEGORIAS_BASE, df_insumos["categoria"].tolist())
    unis_op = opciones(UNIDADES_BASE, df_insumos["unidad"].tolist())

    sub_t1, sub_t2, sub_t3 = st.tabs(["📋 Lista de Insumos", "➕ Agregar / Comprar Insumo", "✏️ Editar / Eliminar Insumo"])

    with sub_t1:
        f1, f2, f3 = st.columns([3, 2, 2])
        busqueda = f1.text_input("🔍 Buscar insumo...", placeholder="Ej. Leche Condensada", key="busq_ins")
        cat_filtro = f2.selectbox("Categoría", ["Todas"] + cats_op, key="cat_filtro_ins")
        solo_crit = f3.checkbox("Solo stock crítico", key="solo_crit")
        if not df_insumos.empty:
            df_mostrar = df_insumos.copy()
            df_mostrar.insert(0, "estado", ["🔴 Crítico" if c else "🟢 OK" for c in mask_crit.tolist()])
            if busqueda:
                df_mostrar = df_mostrar[df_mostrar['nombre'].str.contains(busqueda, case=False, na=False)]
            if cat_filtro != "Todas":
                df_mostrar = df_mostrar[df_mostrar["categoria"] == cat_filtro]
            if solo_crit:
                df_mostrar = df_mostrar[df_mostrar["estado"] == "🔴 Crítico"]
            mostrar_df(
                df_mostrar[["estado", "nombre", "categoria", "stock", "unidad", "stock_minimo", "costo_unidad"]],
                hide_index=True,
                column_config={
                    "estado": "Estado",
                    "nombre": "Insumo",
                    "categoria": "Categoría",
                    "costo_unidad": st.column_config.NumberColumn("Costo/Unidad", format="$%.2f"),
                    "stock": st.column_config.NumberColumn("Stock Actual"),
                    "unidad": "Unidad",
                    "stock_minimo": st.column_config.NumberColumn("Stock Mínimo")
                }
            )
        else:
            st.info("Aún no hay insumos. Ve a **➕ Agregar / Comprar Insumo** para registrar el primero.")

    with sub_t2:
        modo = st.radio("¿Qué deseas hacer?", ["🆕 Registrar insumo nuevo", "🔁 Reabastecer un insumo que ya tengo"],
                        horizontal=True, key="modo_ins")

        if modo.startswith("🆕"):
            with st.form("form_nuevo_insumo", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    nombre = st.text_input("Nombre del insumo")
                    categoria = st.selectbox("Categoría", cats_op, key="nuevo_cat")
                    unidad = st.selectbox("Unidad de medida", unis_op, key="nuevo_uni")
                with col2:
                    costo = st.number_input("Costo por unidad ($)", min_value=0.0, format="%.2f", key="nuevo_costo")
                    cantidad_comprada = st.number_input("Cantidad a agregar al Stock", min_value=0.0, key="nuevo_cant")
                    stock_minimo = st.number_input("Stock mínimo sugerido", min_value=0.0, key="nuevo_min")
                registrar_gasto = st.checkbox("Registrar esta compra como Egreso en Finanzas", value=True, key="nuevo_gasto",
                                              help="Desmárcalo si solo estás cargando tu inventario inicial.")

                if st.form_submit_button("Guardar Insumo", type="primary"):
                    nombre = nombre.strip()
                    if not nombre:
                        st.error("Escribe el nombre del insumo.")
                    elif nombre.lower() in df_insumos["nombre"].str.lower().tolist():
                        st.error(f"«{nombre}» ya existe. Usa la opción **🔁 Reabastecer** para sumarle stock.")
                    else:
                        escribir_fila("insumos", [nombre, categoria, unidad, costo, cantidad_comprada, stock_minimo])
                        if cantidad_comprada > 0 and registrar_gasto:
                            nuevo_id_fin = obtener_nuevo_id(cargar_tabla("finanzas"))
                            escribir_fila("finanzas", [nuevo_id_fin, "Egreso", costo * cantidad_comprada, ahora_str(),
                                                       f"Compra de {cantidad_comprada:g} {unidad} de {nombre}"])
                        aviso(f"✅ Insumo '{nombre}' guardado correctamente.")
                        st.rerun()
        else:
            if df_insumos.empty:
                st.info("Primero registra al menos un insumo nuevo.")
            else:
                ins_re = st.selectbox("Insumo a reabastecer", df_insumos["nombre"].tolist(), key="reab_sel")
                fila_re = df_insumos[df_insumos["nombre"] == ins_re].iloc[0]
                st.caption(f"Stock actual: **{fila_re['stock']:g} {fila_re['unidad']}** · Costo actual: **{dinero(fila_re['costo_unidad'])}**")
                with st.form(f"form_reabastecer_{ins_re}"):
                    c1, c2, c3 = st.columns(3)
                    cant_re = c1.number_input(f"Cantidad comprada ({fila_re['unidad']})", min_value=0.0, step=1.0)
                    costo_re = c2.number_input("Costo por unidad ($)", min_value=0.0, value=float(fila_re["costo_unidad"]), format="%.2f")
                    min_re = c3.number_input("Stock mínimo", min_value=0.0, value=float(fila_re["stock_minimo"]))
                    gasto_re = st.checkbox("Registrar como Egreso en Finanzas", value=True)
                    if st.form_submit_button("Registrar compra", type="primary"):
                        if cant_re <= 0:
                            st.error("Indica una cantidad mayor a 0.")
                        else:
                            idx = df_insumos[df_insumos["nombre"] == ins_re].index[0]
                            df_insumos.loc[idx, "stock"] = float(df_insumos.loc[idx, "stock"]) + cant_re
                            df_insumos.loc[idx, "costo_unidad"] = costo_re
                            df_insumos.loc[idx, "stock_minimo"] = min_re
                            actualizar_tabla("insumos", df_insumos)
                            if gasto_re:
                                nuevo_id_fin = obtener_nuevo_id(cargar_tabla("finanzas"))
                                escribir_fila("finanzas", [nuevo_id_fin, "Egreso", costo_re * cant_re, ahora_str(),
                                                           f"Compra de {cant_re:g} {fila_re['unidad']} de {ins_re}"])
                            aviso(f"✅ Se sumaron {cant_re:g} {fila_re['unidad']} a '{ins_re}'.")
                            st.rerun()

    with sub_t3:
        if not df_insumos.empty:
            insumo_sel = st.selectbox("Selecciona un insumo para modificar o borrar", df_insumos["nombre"].tolist(), key="edit_ins_sel")
            fila_ins = df_insumos[df_insumos["nombre"] == insumo_sel].iloc[0]

            with st.form(f"form_edit_insumo_{insumo_sel}"):
                c1, c2 = st.columns(2)
                with c1:
                    e_nombre = st.text_input("Nombre", value=str(fila_ins["nombre"]))
                    e_cat = st.selectbox("Categoría", cats_op,
                                         index=cats_op.index(fila_ins["categoria"]) if fila_ins["categoria"] in cats_op else len(cats_op) - 1)
                    e_uni = st.text_input("Unidad", value=str(fila_ins["unidad"]))
                with c2:
                    e_costo = st.number_input("Costo Unitario ($)", min_value=0.0, value=float(fila_ins["costo_unidad"]), format="%.2f")
                    e_stock = st.number_input("Stock Actual Exacto", min_value=0.0, value=float(fila_ins["stock"]))
                    e_min = st.number_input("Stock Mínimo", min_value=0.0, value=float(fila_ins["stock_minimo"]))

                confirmar_del = st.checkbox("Confirmo que quiero eliminar este insumo", key=f"conf_del_ins_{insumo_sel}")
                col_btn1, col_btn2 = st.columns(2)
                btn_guardar = col_btn1.form_submit_button("💾 Guardar Cambios", type="primary")
                btn_borrar = col_btn2.form_submit_button("🗑️ Eliminar Insumo")

                if btn_guardar:
                    e_nombre = e_nombre.strip()
                    otros = df_insumos[df_insumos["nombre"] != insumo_sel]["nombre"].str.lower().tolist()
                    if not e_nombre:
                        st.error("El nombre no puede estar vacío.")
                    elif e_nombre.lower() in otros:
                        st.error(f"Ya existe otro insumo llamado «{e_nombre}».")
                    else:
                        idx = df_insumos[df_insumos["nombre"] == insumo_sel].index[0]
                        for col, val in [("nombre", e_nombre), ("categoria", e_cat), ("unidad", e_uni.strip()),
                                         ("costo_unidad", e_costo), ("stock", e_stock), ("stock_minimo", e_min)]:
                            df_insumos.loc[idx, col] = val
                        actualizar_tabla("insumos", df_insumos)
                        # Si cambió el nombre, se actualiza en las recetas para no romperlas
                        if e_nombre != insumo_sel and (df_ri_all["insumo_nombre"] == insumo_sel).any():
                            df_ri_all.loc[df_ri_all["insumo_nombre"] == insumo_sel, "insumo_nombre"] = e_nombre
                            actualizar_tabla("receta_ingredientes", df_ri_all)
                        aviso("✅ Insumo actualizado.")
                        st.rerun()

                if btn_borrar:
                    usos = df_ri_all[df_ri_all["insumo_nombre"] == insumo_sel]
                    if not usos.empty:
                        nombres_rec = df_recetas_all[df_recetas_all["id"].isin(usos["receta_id"])]["nombre"].tolist()
                        st.error(f"No se puede eliminar: «{insumo_sel}» se usa en las recetas: {', '.join(nombres_rec) or 'sin nombre'}. "
                                 "Quítalo primero de esas recetas.")
                    elif not confirmar_del:
                        st.error("Marca la casilla de confirmación para eliminar.")
                    else:
                        actualizar_tabla("insumos", df_insumos[df_insumos["nombre"] != insumo_sel])
                        aviso(f"🗑️ Insumo '{insumo_sel}' eliminado.", "warning")
                        st.rerun()
        else:
            st.info("No hay insumos para editar.")

# ==========================================
# 2. RECETAS
# ==========================================
with tabs[1]:
    df_recetas = cargar_tabla("recetas")
    df_ri = cargar_tabla("receta_ingredientes")
    df_insumos = cargar_tabla("insumos")
    lista_ins = df_insumos["nombre"].tolist()
    mapa_unidad = dict(zip(df_insumos["nombre"], df_insumos["unidad"]))

    sub_r1, sub_r2, sub_r3 = st.tabs(["📖 Ver Recetas", "➕ Nueva Receta", "✏️ Editar / Eliminar Receta"])

    with sub_r1:
        if not df_recetas.empty:
            for _, r in df_recetas.iterrows():
                p_venta = float(r["precio_venta"])
                with st.expander(f"🍰 {r['nombre']} ({r['categoria'] or 'POSTRES'}) - Precio Venta: {dinero(p_venta)}"):
                    filas, costo_estimado, faltan = detalle_receta(r["id"], df_ri, df_insumos)
                    if filas:
                        st.table(pd.DataFrame(filas))
                    else:
                        st.info("Esta receta aún no tiene ingredientes. Agrégalos en la pestaña de edición.")
                    if faltan:
                        st.warning("⚠️ Estos insumos ya no existen en el inventario: " + ", ".join(faltan))

                    ganancia = p_venta - costo_estimado
                    margen = (ganancia / p_venta * 100) if p_venta > 0 else 0
                    col_m1, col_m2, col_m3 = st.columns(3)
                    col_m1.metric("Costo Insumos (1 Tanda)", dinero(costo_estimado))
                    col_m2.metric("Precio de Venta", dinero(p_venta))
                    col_m3.metric("Margen / Ganancia Estimada", dinero(ganancia), f"{margen:.0f}%")
        else:
            st.info("Aún no hay recetas. Crea la primera en **➕ Nueva Receta**.")

    with sub_r2:
        if 'ing_temp' not in st.session_state:
            st.session_state.ing_temp = []
        v = st.session_state.setdefault("rec_v", 0)  # sirve para limpiar el formulario al guardar

        st.subheader("Datos Generales")
        c_r1, c_r2, c_r3 = st.columns(3)
        nom_rec = c_r1.text_input("Nombre de la receta", key=f"nom_rec_{v}")
        cat_rec = c_r2.text_input("Categoría", value="PAYS", key=f"cat_rec_{v}")
        p_venta_rec = c_r3.number_input("Precio de Venta ($)", min_value=0.0, format="%.2f", key=f"pv_rec_{v}")

        st.markdown("---")
        st.subheader("Agregar Ingredientes")
        if not lista_ins:
            st.info("Primero registra insumos en el inventario.")
        else:
            ci1, ci2, ci3 = st.columns([2, 2, 1])
            ins_sel = ci1.selectbox("Insumo", lista_ins, key=f"ins_sel_{v}")
            cant_sel = ci2.number_input(f"Cantidad para 1 tanda ({mapa_unidad.get(ins_sel, '')})",
                                        min_value=0.0, step=0.1, key=f"cant_sel_{v}")
            ci3.write("")
            if ci3.button("➕ Añadir", key=f"btn_add_ing_{v}"):
                if cant_sel > 0:
                    for ing in st.session_state.ing_temp:
                        if ing["insumo_nombre"] == ins_sel:
                            ing["cantidad"] = round(ing["cantidad"] + cant_sel, 4)
                            break
                    else:
                        st.session_state.ing_temp.append({"insumo_nombre": ins_sel, "cantidad": cant_sel})
                    st.rerun()
                else:
                    st.warning("Indica una cantidad mayor a 0.")

        if st.session_state.ing_temp:
            st.markdown("**Ingredientes de la receta:**")
            costo_prev = 0.0
            for i, ing in enumerate(st.session_state.ing_temp):
                d = df_insumos[df_insumos["nombre"] == ing["insumo_nombre"]]
                costo_u = float(d.iloc[0]["costo_unidad"]) if not d.empty else 0.0
                costo_prev += costo_u * ing["cantidad"]
                ca, cb, cc, cd = st.columns([4, 2, 2, 1])
                ca.write(ing["insumo_nombre"])
                cb.write(f"{ing['cantidad']:g} {mapa_unidad.get(ing['insumo_nombre'], '')}")
                cc.write(dinero(costo_u * ing["cantidad"]))
                if cd.button("❌", key=f"quitar_{i}_{v}", help="Quitar este ingrediente"):
                    st.session_state.ing_temp.pop(i)
                    st.rerun()
            st.info(f"💡 Costo estimado: **{dinero(costo_prev)}** · Ganancia estimada: **{dinero(p_venta_rec - costo_prev)}**")

            col_b_g, col_b_c = st.columns(2)
            if col_b_g.button("💾 Guardar Receta Completa", type="primary", key=f"btn_save_rec_{v}"):
                nombre_limpio = nom_rec.strip()
                if not nombre_limpio:
                    st.error("Ingresa el nombre de la receta.")
                elif nombre_limpio.lower() in df_recetas["nombre"].str.lower().tolist():
                    st.error("Ya existe una receta con ese nombre.")
                else:
                    nuevo_id = obtener_nuevo_id(df_recetas)
                    escribir_fila("recetas", [nuevo_id, nombre_limpio, cat_rec.strip().upper(), p_venta_rec])
                    escribir_filas("receta_ingredientes",
                                   [[nuevo_id, ing['insumo_nombre'], ing['cantidad']] for ing in st.session_state.ing_temp])
                    st.session_state.ing_temp = []
                    st.session_state.rec_v = v + 1
                    aviso("✅ Receta guardada exitosamente.")
                    st.rerun()
            if col_b_c.button("Limpiar Ingredientes", key=f"btn_clear_ing_{v}"):
                st.session_state.ing_temp = []
                st.rerun()

    with sub_r3:
        if not df_recetas.empty:
            etiquetas_r = {int(r.id): f"{r.id} - {r.nombre}" for r in df_recetas.itertuples()}
            receta_edit_id = st.selectbox("Selecciona la receta a modificar", list(etiquetas_r), format_func=lambda x: etiquetas_r[x], key="edit_rec_sel")
            rec_row = df_recetas[df_recetas["id"] == receta_edit_id].iloc[0]

            with st.form(f"form_edit_receta_{receta_edit_id}"):
                ce1, ce2, ce3 = st.columns(3)
                er_nombre = ce1.text_input("Nombre Receta", value=str(rec_row["nombre"]))
                er_cat = ce2.text_input("Categoría", value=str(rec_row["categoria"]))
                er_precio = ce3.number_input("Precio Venta ($)", value=float(rec_row["precio_venta"]), min_value=0.0, format="%.2f")
                conf_del_rec = st.checkbox("Confirmo que quiero eliminar esta receta completa", key=f"conf_del_rec_{receta_edit_id}")

                cbr1, cbr2 = st.columns(2)
                b_edit_rec = cbr1.form_submit_button("💾 Actualizar Encabezado Receta", type="primary")
                b_del_rec = cbr2.form_submit_button("🗑️ Eliminar Receta Completa")

                if b_edit_rec:
                    er_nombre = er_nombre.strip()
                    otros = df_recetas[df_recetas["id"] != receta_edit_id]["nombre"].str.lower().tolist()
                    if not er_nombre:
                        st.error("El nombre no puede estar vacío.")
                    elif er_nombre.lower() in otros:
                        st.error("Ya existe otra receta con ese nombre.")
                    else:
                        idx = df_recetas[df_recetas["id"] == receta_edit_id].index[0]
                        nombre_anterior = rec_row["nombre"]
                        df_recetas.loc[idx, "nombre"] = er_nombre
                        df_recetas.loc[idx, "categoria"] = er_cat.strip().upper()
                        df_recetas.loc[idx, "precio_venta"] = er_precio
                        actualizar_tabla("recetas", df_recetas)
                        # Mantener enlazados los lotes ya producidos
                        if er_nombre != nombre_anterior:
                            df_t = cargar_tabla("tandas")
                            if (df_t["receta_nombre"] == nombre_anterior).any():
                                df_t.loc[df_t["receta_nombre"] == nombre_anterior, "receta_nombre"] = er_nombre
                                actualizar_tabla("tandas", df_t)
                        aviso("✅ Datos de receta actualizados.")
                        st.rerun()

                if b_del_rec:
                    if not conf_del_rec:
                        st.error("Marca la casilla de confirmación para eliminar.")
                    else:
                        actualizar_tabla("recetas", df_recetas[df_recetas["id"] != receta_edit_id])
                        actualizar_tabla("receta_ingredientes", df_ri[df_ri["receta_id"] != receta_edit_id])
                        aviso("🗑️ Receta eliminada.", "warning")
                        st.rerun()

            # ---- Edición de ingredientes (antes no se podían modificar) ----
            st.markdown("#### 🧂 Ingredientes de esta receta")
            st.caption("Puedes cambiar cantidades, agregar filas con ➕ o borrarlas seleccionándolas y presionando Supr.")
            ing_actual = df_ri[df_ri["receta_id"] == receta_edit_id][["insumo_nombre", "cantidad"]].reset_index(drop=True)
            ing_editado = editor_df(
                ing_actual, key=f"ed_ing_{receta_edit_id}", num_rows="dynamic", hide_index=True,
                column_config={
                    "insumo_nombre": st.column_config.SelectboxColumn("Insumo", options=lista_ins, required=True),
                    "cantidad": st.column_config.NumberColumn("Cantidad (1 tanda)", min_value=0.0, required=True),
                })
            if st.button("💾 Guardar Ingredientes", type="primary", key=f"save_ing_{receta_edit_id}"):
                limpio = ing_editado.dropna(subset=["insumo_nombre", "cantidad"])
                limpio = limpio[limpio["cantidad"] > 0]
                limpio = limpio.groupby("insumo_nombre", as_index=False, sort=False)["cantidad"].sum()
                limpio.insert(0, "receta_id", int(receta_edit_id))
                nuevo_ri = pd.concat([df_ri[df_ri["receta_id"] != receta_edit_id], limpio], ignore_index=True)
                actualizar_tabla("receta_ingredientes", nuevo_ri)
                aviso("✅ Ingredientes actualizados.")
                st.rerun()
        else:
            st.info("No hay recetas para editar.")

# ==========================================
# 3. REGISTRAR TANDA (PRODUCCIÓN)
# ==========================================
with tabs[2]:
    df_recetas = cargar_tabla("recetas")
    df_insumos = cargar_tabla("insumos")
    df_tandas = cargar_tabla("tandas")
    df_ri = cargar_tabla("receta_ingredientes")
    tv = st.session_state.setdefault("tanda_v", 0)

    st.subheader("📝 Registrar Tanda Producida")
    if not df_recetas.empty:
        c_t1, c_t2 = st.columns(2)
        receta_sel = c_t1.selectbox("Seleccionar Receta", df_recetas["nombre"].tolist(), key="tanda_receta")
        cant_preparar = c_t2.number_input("Número de Tandas a Preparar", min_value=1, step=1, value=1, key="tanda_cant")
        notas_tanda = st.text_input("Notas de la tanda (Opcional)", placeholder="Ej. Lote horneado por la mañana", key=f"tanda_notas_{tv}")

        receta_row = df_recetas[df_recetas["nombre"] == receta_sel].iloc[0]
        ingredientes = df_ri[df_ri["receta_id"] == receta_row["id"]]

        st.markdown("#### Verificación de Insumos:")
        suficiente = True
        costo_total_tanda = 0.0

        if ingredientes.empty:
            st.warning("Esta receta no tiene ingredientes. Agrégalos en **📖 Recetas → Editar**.")
            suficiente = False

        for _, row in ingredientes.iterrows():
            ins_nom = row["insumo_nombre"]
            cant_nec = float(row["cantidad"]) * cant_preparar
            ins_data = df_insumos[df_insumos["nombre"] == ins_nom]

            if ins_data.empty:
                st.error(f"❌ {ins_nom}: ya no existe en el inventario.")
                suficiente = False
                continue
            stock_act = float(ins_data.iloc[0]['stock'])
            costo_u = float(ins_data.iloc[0]['costo_unidad'])
            unidad = ins_data.iloc[0]['unidad']
            costo_total_tanda += cant_nec * costo_u

            if stock_act < cant_nec:
                st.error(f"❌ {ins_nom}: Necesitas {cant_nec:g} {unidad} (Disponible: {stock_act:g})")
                suficiente = False
            else:
                st.success(f"✅ {ins_nom}: {cant_nec:g} {unidad} (quedarán {stock_act - cant_nec:g})")

        if suficiente:
            st.info(f"💰 Costo de esta producción: **{dinero(costo_total_tanda)}** "
                    f"({dinero(costo_total_tanda / cant_preparar)} por tanda)")

        if st.button("🍳 Confirmar y Producir Tanda", type="primary", disabled=not suficiente, key="btn_producir"):
            for _, row in ingredientes.iterrows():
                mask = df_insumos["nombre"] == row["insumo_nombre"]
                df_insumos.loc[mask, "stock"] = df_insumos.loc[mask, "stock"].astype(float) - float(row["cantidad"]) * cant_preparar
            actualizar_tabla("insumos", df_insumos)

            nuevo_tanda_id = obtener_nuevo_id(df_tandas)
            escribir_fila("tandas", [nuevo_tanda_id, receta_sel, int(cant_preparar), int(cant_preparar),
                                     costo_total_tanda, ahora().strftime("%Y-%m-%d %H:%M"), notas_tanda])
            st.session_state.tanda_v = tv + 1
            aviso(f"✅ Tanda #{nuevo_tanda_id} registrada ({int(cant_preparar)} × {receta_sel}) e inventario descontado.")
            st.rerun()
    else:
        st.info("No hay recetas registradas para producir.")

    st.write("---")
    st.subheader("✏️ Modificar / Corregir Stock de Tandas Registradas")
    if not df_tandas.empty:
        df_t_ord = df_tandas.sort_values("id", ascending=False)
        etiquetas_t = {int(r.id): f"Lote #{r.id} - {r.receta_nombre} ({r.fecha}) · disp: {r.stock_disponible:g}" for r in df_t_ord.itertuples()}
        tanda_edit_id = st.selectbox("Selecciona Lote / Tanda a modificar", list(etiquetas_t), format_func=lambda x: etiquetas_t[x], key="edit_tanda_sel")
        fila_tanda = df_tandas[df_tandas["id"] == tanda_edit_id].iloc[0]

        with st.form(f"form_edit_tanda_{tanda_edit_id}"):
            ct1, ct2, ct3 = st.columns(3)
            e_disp = ct1.number_input("Stock Disponible Actual", min_value=0, step=1, value=int(round(float(fila_tanda["stock_disponible"]))))
            e_notas = ct2.text_input("Notas", value=str(fila_tanda["notas"]))
            e_costo_t = ct3.number_input("Costo Total Tanda ($)", min_value=0.0, value=float(fila_tanda["costo_total"]), format="%.2f")
            devolver = st.checkbox("Al borrar: devolver los insumos al inventario (úsalo si la tanda se registró por error)",
                                   key=f"devolver_{tanda_edit_id}")
            conf_del_t = st.checkbox("Confirmo que quiero borrar este lote", key=f"conf_del_t_{tanda_edit_id}")

            cb1, cb2 = st.columns(2)
            guardar_t = cb1.form_submit_button("💾 Actualizar Tanda", type="primary")
            borrar_t = cb2.form_submit_button("🗑️ Borrar Registro de Tanda")

            if guardar_t:
                idx_t = df_tandas[df_tandas["id"] == tanda_edit_id].index[0]
                df_tandas.loc[idx_t, "stock_disponible"] = e_disp
                df_tandas.loc[idx_t, "notas"] = e_notas
                df_tandas.loc[idx_t, "costo_total"] = e_costo_t
                actualizar_tabla("tandas", df_tandas)
                aviso("✅ Tanda actualizada.")
                st.rerun()

            if borrar_t:
                if not conf_del_t:
                    st.error("Marca la casilla de confirmación para borrar.")
                else:
                    if devolver:
                        rec = df_recetas[df_recetas["nombre"] == fila_tanda["receta_nombre"]]
                        if rec.empty:
                            st.warning("La receta de este lote ya no existe; no se pudieron devolver los insumos.")
                        else:
                            for _, ing in df_ri[df_ri["receta_id"] == rec.iloc[0]["id"]].iterrows():
                                mask = df_insumos["nombre"] == ing["insumo_nombre"]
                                df_insumos.loc[mask, "stock"] = df_insumos.loc[mask, "stock"].astype(float) + \
                                    float(ing["cantidad"]) * float(fila_tanda["cantidad_producida"])
                            actualizar_tabla("insumos", df_insumos)
                    actualizar_tabla("tandas", df_tandas[df_tandas["id"] != tanda_edit_id])
                    aviso("🗑️ Registro de tanda eliminado.", "warning")
                    st.rerun()

# ==========================================
# 4. LISTA DE COMPRAS
# ==========================================
with tabs[3]:
    st.subheader("📋 Lista Automática de Compras")
    df_insumos = cargar_tabla("insumos")

    if not df_insumos.empty:
        faltantes = df_insumos[df_insumos['stock'] <= df_insumos['stock_minimo']].copy()

        if faltantes.empty:
            st.success("✔️ ¡Inventario completo! Todos los insumos están sobre el nivel mínimo.")
        else:
            faltantes['sugerido_comprar'] = (faltantes['stock_minimo'] - faltantes['stock']).clip(lower=1)
            faltantes['costo_estimado_compra'] = faltantes['sugerido_comprar'] * faltantes['costo_unidad']
            total_compra = float(faltantes['costo_estimado_compra'].sum())

            m1, m2 = st.columns(2)
            m1.metric("Insumos por comprar", len(faltantes))
            m2.metric("Inversión estimada", dinero(total_compra))

            mostrar_df(
                faltantes[['nombre', 'categoria', 'stock', 'stock_minimo', 'sugerido_comprar', 'unidad', 'costo_estimado_compra']],
                hide_index=True,
                column_config={
                    "nombre": "Insumo",
                    "categoria": "Categoría",
                    "stock": "Stock Actual",
                    "stock_minimo": "Mínimo",
                    "sugerido_comprar": "Sugerido Comprar",
                    "unidad": "Unidad",
                    "costo_estimado_compra": st.column_config.NumberColumn("Costo Estimado", format="$%.2f")
                }
            )
            st.caption("Cuando compres, ve a **📦 Inventario → ➕ Agregar / Comprar → Reabastecer** para sumar el stock.")

            txt_compras = "LISTA DE COMPRAS - LADY PAYS\n" + f"Fecha: {ahora().strftime('%Y-%m-%d')}\n" + "-" * 35 + "\n"
            for _, f in faltantes.iterrows():
                txt_compras += f"- {f['nombre']}: Comprar {f['sugerido_comprar']:g} {f['unidad']} (Est. ${f['costo_estimado_compra']:.2f})\n"
            txt_compras += "-" * 35 + f"\nTOTAL ESTIMADO: ${total_compra:,.2f}\n"

            st.download_button("📥 Descargar Lista (.txt)", data=txt_compras, file_name="lista_compras.txt", key="dl_compras")
    else:
        st.info("No hay insumos registrados todavía.")

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
        df_disp = df_tandas[df_tandas["stock_disponible"] >= 1]

        if not df_disp.empty:
            etiquetas_v = {int(r.id): f"Lote #{r.id} - {r.receta_nombre} (Disp: {r.stock_disponible:g})" for r in df_disp.itertuples()}
            tanda_vender_id = st.selectbox("Selecciona Lote a Vender", list(etiquetas_v), format_func=lambda x: etiquetas_v[x], key="venta_lote")
            tanda_row = df_disp[df_disp["id"] == tanda_vender_id].iloc[0]
            max_cant = int(float(tanda_row["stock_disponible"]))

            precio_default = 0.0
            rec_p = df_recetas[df_recetas["nombre"] == tanda_row["receta_nombre"]]
            if not rec_p.empty:
                precio_default = float(rec_p.iloc[0]["precio_venta"])
            else:
                st.warning("La receta de este lote ya no existe; escribe el precio manualmente.")

            cv1, cv2 = st.columns(2)
            cant_vender = cv1.number_input(f"Cantidad a vender ({tanda_row['receta_nombre']})", min_value=1, max_value=max_cant,
                                           step=1, value=1, key=f"venta_cant_{tanda_vender_id}")
            precio_v = cv2.number_input("Precio unitario ($)", min_value=0.0, value=precio_default, step=1.0, format="%.2f",
                                        key=f"venta_precio_{tanda_vender_id}", help="Puedes cambiarlo si hiciste un descuento.")
            nota_venta = st.text_input("Nota (opcional)", placeholder="Ej. Cliente: María", key=f"venta_nota_{tanda_vender_id}")
            ingreso_total = cant_vender * precio_v
            st.info(f"Total de la venta: **{dinero(ingreso_total)}**")

            if st.button("💰 Registrar Venta", type="primary", key="btn_venta"):
                descripcion = f"Venta de {cant_vender} {tanda_row['receta_nombre']} (Lote #{tanda_vender_id})"
                if nota_venta.strip():
                    descripcion += f" - {nota_venta.strip()}"
                nuevo_id_f = obtener_nuevo_id(df_finanzas)
                escribir_fila("finanzas", [nuevo_id_f, "Ingreso", ingreso_total, ahora_str(), descripcion])

                idx_t = df_tandas[df_tandas["id"] == tanda_vender_id].index[0]
                df_tandas.loc[idx_t, "stock_disponible"] = float(df_tandas.loc[idx_t, "stock_disponible"]) - cant_vender
                actualizar_tabla("tandas", df_tandas)

                aviso(f"✅ Venta registrada. +{dinero(ingreso_total)}")
                st.rerun()
        else:
            st.info("No hay postres/tandas disponibles para venta. Registra una producción primero.")

        st.write("---")
        st.subheader("➕ Registro Manual de Ingreso / Egreso")
        with st.form("form_finanza_manual", clear_on_submit=True):
            tipo_m = st.selectbox("Tipo Movimiento", ["Ingreso", "Egreso"], key="man_tipo")
            monto_m = st.number_input("Monto ($)", min_value=0.0, format="%.2f", key="man_monto")
            desc_m = st.text_input("Descripción / Motivo", key="man_desc")

            if st.form_submit_button("Guardar Movimiento"):
                if monto_m > 0 and desc_m.strip():
                    n_id = obtener_nuevo_id(df_finanzas)
                    escribir_fila("finanzas", [n_id, tipo_m, monto_m, ahora_str(), desc_m.strip()])
                    aviso("✅ Movimiento registrado.")
                    st.rerun()
                else:
                    st.error("Indica un monto mayor a 0 y una descripción.")

    with col_f:
        st.subheader("📈 Balance Financiero")
        if not df_finanzas.empty:
            periodo = st.selectbox("Periodo", ["Todo", "Hoy", "Últimos 7 días", "Este mes"], key="fin_periodo")
            fechas = pd.to_datetime(df_finanzas["fecha"], errors="coerce")
            hoy_ts = pd.Timestamp(ahora().date())
            if periodo == "Hoy":
                m = fechas.dt.normalize() == hoy_ts
            elif periodo == "Últimos 7 días":
                m = fechas.dt.normalize() >= hoy_ts - pd.Timedelta(days=6)
            elif periodo == "Este mes":
                m = (fechas.dt.year == hoy_ts.year) & (fechas.dt.month == hoy_ts.month)
            else:
                m = pd.Series(True, index=df_finanzas.index)
            df_fil = df_finanzas[m]

            ingresos = df_fil[df_fil['tipo'] == 'Ingreso']['monto'].sum()
            egresos = df_fil[df_fil['tipo'] == 'Egreso']['monto'].sum()
            balance = ingresos - egresos

            cm1, cm2, cm3 = st.columns(3)
            cm1.metric("Ingresos", dinero(ingresos))
            cm2.metric("Egresos", dinero(egresos))
            cm3.metric("Balance Neto", dinero(balance))
            st.caption("Las compras de insumos ya se anotan como Egreso, por eso las mermas no se restan otra vez.")

            mostrar_df(df_fil.sort_values("id", ascending=False), height=220, hide_index=True,
                       column_config={"monto": st.column_config.NumberColumn("Monto", format="$%.2f")})
            st.download_button("📥 Descargar movimientos (CSV)", data=df_fil.to_csv(index=False).encode("utf-8-sig"),
                               file_name="finanzas_ladypays.csv", mime="text/csv", key="dl_fin")

            st.markdown("#### ✏️ Modificar / Eliminar Transacción")
            etiquetas_f = {int(r.id): f"#{r.id} · {r.fecha} · {r.tipo} {dinero(r.monto)} · {r.descripcion[:35]}"
                           for r in df_finanzas.sort_values("id", ascending=False).itertuples()}
            trans_id = st.selectbox("Selecciona Transacción", list(etiquetas_f), format_func=lambda x: etiquetas_f[x], key="fin_edit_sel")
            f_trans = df_finanzas[df_finanzas["id"] == trans_id].iloc[0]
            with st.form(f"form_edit_finanza_{trans_id}"):
                ef_tipo = st.selectbox("Tipo", ["Ingreso", "Egreso"], index=0 if f_trans["tipo"] == "Ingreso" else 1)
                ef_monto = st.number_input("Monto ($)", min_value=0.0, value=float(f_trans["monto"]), format="%.2f")
                ef_desc = st.text_input("Descripción", value=str(f_trans["descripcion"]))
                conf_del_f = st.checkbox("Confirmo que quiero eliminar esta transacción", key=f"conf_del_f_{trans_id}")
                st.caption("Nota: editar o borrar una venta aquí NO modifica el stock del lote; corrígelo en 🍳 Registrar Tanda.")

                col_fb1, col_fb2 = st.columns(2)
                if col_fb1.form_submit_button("💾 Actualizar", type="primary"):
                    idx_f = df_finanzas[df_finanzas["id"] == trans_id].index[0]
                    df_finanzas.loc[idx_f, "tipo"] = ef_tipo
                    df_finanzas.loc[idx_f, "monto"] = ef_monto
                    df_finanzas.loc[idx_f, "descripcion"] = ef_desc
                    actualizar_tabla("finanzas", df_finanzas)
                    aviso("✅ Transacción actualizada.")
                    st.rerun()

                if col_fb2.form_submit_button("🗑️ Eliminar"):
                    if not conf_del_f:
                        st.error("Marca la casilla de confirmación para eliminar.")
                    else:
                        actualizar_tabla("finanzas", df_finanzas[df_finanzas["id"] != trans_id])
                        aviso("🗑️ Transacción eliminada.", "warning")
                        st.rerun()
        else:
            st.info("Aún no hay movimientos financieros.")

# ==========================================
# 6. MERMAS Y PÉRDIDAS
# ==========================================
with tabs[5]:
    df_mermas = cargar_tabla("mermas")
    df_insumos = cargar_tabla("insumos")
    df_tandas = cargar_tabla("tandas")

    st.subheader("🗑️ Registro de Mermas y Pérdidas")
    tipo_merma = st.radio("Tipo de pérdida:", ["Insumo (Materia prima)", "Producto Terminado (Pays / Postres)"],
                          horizontal=True, key="tipo_merma")

    if tipo_merma == "Insumo (Materia prima)":
        df_con_stock = df_insumos[df_insumos["stock"] > 0]
        if not df_con_stock.empty:
            # El selector va FUERA del formulario para que unidad y máximo se actualicen al cambiar de insumo
            ins_m_sel = st.selectbox("Insumo dañado", df_con_stock["nombre"].tolist(), key="merma_ins_sel")
            ins_row = df_con_stock[df_con_stock["nombre"] == ins_m_sel].iloc[0]
            st.caption(f"Disponible: {ins_row['stock']:g} {ins_row['unidad']}")
            with st.form(f"form_merma_insumo_{ins_m_sel}", clear_on_submit=True):
                cant_p = st.number_input(f"Cantidad perdida ({ins_row['unidad']})", min_value=0.0,
                                         max_value=float(ins_row['stock']), step=0.1)
                motivo_p = st.text_input("Motivo de la pérdida")

                if st.form_submit_button("Registrar Merma de Insumo", type="primary"):
                    if cant_p > 0:
                        idx_i = df_insumos[df_insumos["nombre"] == ins_m_sel].index[0]
                        df_insumos.loc[idx_i, "stock"] = float(df_insumos.loc[idx_i, "stock"]) - cant_p
                        actualizar_tabla("insumos", df_insumos)

                        costo_p = cant_p * float(ins_row["costo_unidad"])
                        m_id = obtener_nuevo_id(df_mermas)
                        escribir_fila("mermas", [m_id, "Insumo", ins_m_sel, cant_p, ins_row['unidad'], costo_p,
                                                 ahora().strftime("%Y-%m-%d %H:%M"), motivo_p])
                        aviso(f"✅ Merma registrada (pérdida estimada {dinero(costo_p)}).")
                        st.rerun()
                    else:
                        st.error("Indica una cantidad mayor a 0.")
        else:
            st.info("No hay insumos con stock para registrar mermas.")
    else:
        df_disp_m = df_tandas[df_tandas["stock_disponible"] >= 1]
        if not df_disp_m.empty:
            etiquetas_m = {int(r.id): f"Lote #{r.id} - {r.receta_nombre} (Disp: {r.stock_disponible:g})" for r in df_disp_m.itertuples()}
            tanda_m_id = st.selectbox("Lote afectado", list(etiquetas_m), format_func=lambda x: etiquetas_m[x], key="merma_lote_sel")
            tanda_m_row = df_disp_m[df_disp_m["id"] == tanda_m_id].iloc[0]
            with st.form(f"form_merma_producto_{tanda_m_id}", clear_on_submit=True):
                cant_prod_p = st.number_input("Cantidad dañada (Piezas)", min_value=1,
                                              max_value=int(float(tanda_m_row["stock_disponible"])), step=1, value=1)
                motivo_prod_p = st.text_input("Motivo de la pérdida")

                if st.form_submit_button("Registrar Merma de Producto", type="primary"):
                    idx_t = df_tandas[df_tandas["id"] == tanda_m_id].index[0]
                    df_tandas.loc[idx_t, "stock_disponible"] = float(df_tandas.loc[idx_t, "stock_disponible"]) - cant_prod_p
                    actualizar_tabla("tandas", df_tandas)

                    cant_prod = float(tanda_m_row["cantidad_producida"])
                    costo_un = float(tanda_m_row["costo_total"]) / cant_prod if cant_prod > 0 else 0
                    m_id = obtener_nuevo_id(df_mermas)
                    escribir_fila("mermas", [m_id, "Producto Terminado", tanda_m_row["receta_nombre"], cant_prod_p, "piezas",
                                             cant_prod_p * costo_un, ahora().strftime("%Y-%m-%d %H:%M"), motivo_prod_p])
                    aviso(f"✅ Merma de producto registrada (pérdida estimada {dinero(cant_prod_p * costo_un)}).")
                    st.rerun()
        else:
            st.info("No hay productos terminados disponibles.")

    st.write("---")
    st.subheader("📜 Historial y Modificación de Mermas")
    if not df_mermas.empty:
        st.metric("Pérdida total estimada por mermas", dinero(df_mermas["costo_estimado"].sum()))
        mostrar_df(df_mermas.sort_values("id", ascending=False), hide_index=True,
                   column_config={"costo_estimado": st.column_config.NumberColumn("Costo Estimado", format="$%.2f")})

        etiquetas_mm = {int(r.id): f"#{r.id} · {r.fecha} · {r.nombre} ({r.cantidad:g} {r.unidad})"
                        for r in df_mermas.sort_values("id", ascending=False).itertuples()}
        merma_edit_id = st.selectbox("Selecciona la merma a modificar/eliminar", list(etiquetas_mm),
                                     format_func=lambda x: etiquetas_mm[x], key="merma_edit_sel")
        m_row = df_mermas[df_mermas["id"] == merma_edit_id].iloc[0]
        with st.form(f"form_edit_merma_{merma_edit_id}"):
            em_cant = st.number_input("Cantidad", min_value=0.0, value=float(m_row["cantidad"]))
            em_costo = st.number_input("Costo Estimado ($)", min_value=0.0, value=float(m_row["costo_estimado"]), format="%.2f")
            em_motivo = st.text_input("Motivo", value=str(m_row["motivo"]))
            st.caption("Nota: modificar una merma no reajusta el inventario.")
            restaurar = False
            if m_row["tipo"] == "Insumo":
                restaurar = st.checkbox("Al eliminar: devolver la cantidad al inventario del insumo", key=f"rest_merma_{merma_edit_id}")
            conf_del_m = st.checkbox("Confirmo que quiero eliminar esta merma", key=f"conf_del_m_{merma_edit_id}")

            col_mb1, col_mb2 = st.columns(2)
            if col_mb1.form_submit_button("💾 Actualizar Merma", type="primary"):
                idx_m = df_mermas[df_mermas["id"] == merma_edit_id].index[0]
                df_mermas.loc[idx_m, "cantidad"] = em_cant
                df_mermas.loc[idx_m, "costo_estimado"] = em_costo
                df_mermas.loc[idx_m, "motivo"] = em_motivo
                actualizar_tabla("mermas", df_mermas)
                aviso("✅ Merma actualizada.")
                st.rerun()

            if col_mb2.form_submit_button("🗑️ Eliminar Merma"):
                if not conf_del_m:
                    st.error("Marca la casilla de confirmación para eliminar.")
                else:
                    if restaurar:
                        mask = df_insumos["nombre"] == m_row["nombre"]
                        if mask.any():
                            df_insumos.loc[mask, "stock"] = df_insumos.loc[mask, "stock"].astype(float) + float(m_row["cantidad"])
                            actualizar_tabla("insumos", df_insumos)
                        else:
                            st.warning("Ese insumo ya no existe; no se pudo devolver al inventario.")
                    actualizar_tabla("mermas", df_mermas[df_mermas["id"] != merma_edit_id])
                    aviso("🗑️ Merma eliminada.", "warning")
                    st.rerun()
    else:
        st.info("Aún no hay mermas registradas. ¡Qué bueno! 🎉")

# ==========================================
# 7. RESPALDOS Y AJUSTES
# ==========================================
with tabs[6]:
    st.subheader("⚙️ Copia de Seguridad y Mantenimiento")

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("#### 💾 Exportar Copia de Seguridad")
        datos = {nombre: cargar_tabla(nombre).to_dict(orient="records") for nombre in ESQUEMA}
        st.download_button("⬇️ Descargar Respaldo (JSON)",
                           data=json.dumps(datos, indent=4, ensure_ascii=False, default=str),
                           file_name=f"respaldo_ladypays_{ahora().strftime('%Y%m%d')}.json",
                           mime="application/json", key="dl_respaldo")
        st.caption("Te recomendamos descargar uno cada semana.")

        st.markdown("#### ♻️ Restaurar desde un respaldo")
        archivo = st.file_uploader("Sube un respaldo (.json)", type="json", key="up_respaldo")
        if archivo is not None:
            try:
                datos_subidos = json.load(archivo)
                valido = isinstance(datos_subidos, dict) and any(n in datos_subidos for n in ESQUEMA)
            except Exception:
                valido = False
            if not valido:
                st.error("El archivo no parece un respaldo válido de Lady Pays.")
            else:
                conf_rest = st.checkbox("Entiendo que esto REEMPLAZARÁ los datos actuales", key="conf_rest")
                if st.button("♻️ Restaurar respaldo", disabled=not conf_rest, key="btn_restaurar"):
                    for nombre in ESQUEMA:
                        if nombre in datos_subidos:
                            actualizar_tabla(nombre, _normalizar(pd.DataFrame(datos_subidos[nombre]), nombre))
                    aviso("✅ Respaldo restaurado correctamente.")
                    st.rerun()

    with col_b2:
        st.markdown("#### ⚠️ Restablecer Datos Iniciales")
        st.caption("Borra TODA la información. Descarga un respaldo antes de continuar.")
        confirmacion = st.text_input("Escribe 'BORRAR' para reiniciar el sistema", key="txt_borrar")
        if st.button("Formatear Sistema", type="primary", key="btn_formatear"):
            if confirmacion != "BORRAR":
                st.error("Debes escribir exactamente BORRAR para confirmar.")
            else:
                for nombre in ESQUEMA:
                    actualizar_tabla(nombre, pd.DataFrame(columns=list(ESQUEMA[nombre])))
                aviso("Sistema restablecido de fábrica.", "warning")
                st.rerun()
