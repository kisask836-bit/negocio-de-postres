import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

# ==========================================
# 1. CONFIGURACIÓN DE LA BASE DE DATOS
# ==========================================
def init_db():
    conn = sqlite3.connect('mi_negocio.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS insumos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT, unidad TEXT, costo_unidad REAL, stock REAL, stock_minimo REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS recetas (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT, precio_venta REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS receta_ingredientes (receta_id INTEGER, insumo_id INTEGER, cantidad REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tandas (id INTEGER PRIMARY KEY AUTOINCREMENT, receta_id INTEGER, cantidad_producida INTEGER, stock_disponible INTEGER, costo_total REAL, fecha TEXT, notas TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS finanzas (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, monto REAL, fecha TEXT, descripcion TEXT)''')
    conn.commit()
    conn.close()

init_db()

def get_connection():
    return sqlite3.connect('mi_negocio.db')

# ==========================================
# 2. INTERFAZ DE USUARIO (STREAMLIT)
# ==========================================
st.set_page_config(page_title="Gestión de Postres", layout="wide")
st.title("🍰 Sistema de Gestión - Postres y Pays")

menu = st.sidebar.selectbox("Menú Principal", ["📦 Inventario y Compras", "📖 Recetas", "🥣 Producción (Tandas)", "💰 Ventas", "📊 Finanzas"])

if menu == "📦 Inventario y Compras":
    st.header("Gestión de Insumos")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Registrar/Comprar Insumo")
        nombre = st.text_input("Nombre del insumo (ej. Harina, Leche)")
        unidad = st.selectbox("Unidad de medida", ["Kg", "Litro", "Gramo", "Mililitro", "Pieza"])
        costo = st.number_input("Costo por unidad ($)", min_value=0.0, format="%.2f")
        cantidad_comprada = st.number_input("Cantidad a ingresar/comprar", min_value=0.0)
        stock_minimo = st.number_input("Avisarme cuando quede menos de:", min_value=0.0)
        
        if st.button("Guardar Insumo / Registrar Compra"):
            if nombre:
                conn = get_connection()
                c = conn.cursor()
                c.execute("SELECT id, stock FROM insumos WHERE nombre=?", (nombre,))
                existe = c.fetchone()
                
                if existe:
                    nuevo_stock = existe[1] + cantidad_comprada
                    c.execute("UPDATE insumos SET stock=?, costo_unidad=?, stock_minimo=? WHERE id=?", (nuevo_stock, costo, stock_minimo, existe[0]))
                else:
                    c.execute("INSERT INTO insumos (nombre, unidad, costo_unidad, stock, stock_minimo) VALUES (?, ?, ?, ?, ?)", (nombre, unidad, costo, cantidad_comprada, stock_minimo))
                
                gasto_total = costo * cantidad_comprada
                fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("INSERT INTO finanzas (tipo, monto, fecha, descripcion) VALUES (?, ?, ?, ?)", ("Egreso", gasto_total, fecha_actual, f"Compra de insumo: {cantidad_comprada} {unidad} de {nombre}"))
                conn.commit()
                conn.close()
                st.success(f"✅ {nombre} guardado y gasto registrado.")
    
    with col2:
        st.subheader("Inventario Actual")
        conn = get_connection()
        df_insumos = pd.read_sql_query("SELECT nombre as Insumo, stock as Cantidad, unidad as Unidad, costo_unidad as Costo, stock_minimo as Alerta FROM insumos", conn)
        alertas = df_insumos[df_insumos['Cantidad'] <= df_insumos['Alerta']]
        if not alertas.empty:
            for index, row in alertas.iterrows():
                st.warning(f"⚠️ Te queda poco/a {row['Insumo']} ({row['Cantidad']} {row['Unidad']}).")
        st.dataframe(df_insumos, use_container_width=True)
        conn.close()

elif menu == "📖 Recetas":
    st.header("Libro de Recetas")
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, nombre FROM insumos")
    nombres_insumos = {f"{item[1]}": item[0] for item in c.fetchall()}

    if 'ingredientes_temp' not in st.session_state:
        st.session_state.ingredientes_temp = []

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Crear Nueva Receta")
        nombre_receta = st.text_input("Nombre del producto (ej. Pay de Limón)")
        precio_venta = st.number_input("Precio de venta ($)", min_value=0.0, format="%.2f")
        insumo_sel = st.selectbox("Selecciona un insumo", list(nombres_insumos.keys()) if nombres_insumos else ["Vacio"])
        cant_insumo = st.number_input("Cantidad requerida por receta", min_value=0.0)
        
        if st.button("Añadir Ingrediente a la lista"):
            if insumo_sel != "Vacio":
                st.session_state.ingredientes_temp.append({"id": nombres_insumos[insumo_sel], "nombre": insumo_sel, "cantidad": cant_insumo})
                st.success(f"Añadido {cant_insumo} de {insumo_sel}")

        if st.button("💾 Guardar Receta Completa"):
            if nombre_receta and st.session_state.ingredientes_temp:
                c.execute("INSERT INTO recetas (nombre, precio_venta) VALUES (?, ?)", (nombre_receta, precio_venta))
                receta_id = c.lastrowid
                for ing in st.session_state.ingredientes_temp:
                    c.execute("INSERT INTO receta_ingredientes (receta_id, insumo_id, cantidad) VALUES (?, ?, ?)", (receta_id, ing['id'], ing['cantidad']))
                conn.commit()
                st.session_state.ingredientes_temp = []
                st.success("✅ Receta guardada.")
    
    with col2:
        st.subheader("Ingredientes en esta receta:")
        if st.session_state.ingredientes_temp:
            st.table(pd.DataFrame(st.session_state.ingredientes_temp)[['nombre', 'cantidad']])
            if st.button("Limpiar Lista"):
                st.session_state.ingredientes_temp = []
                st.rerun()
    conn.close()

elif menu == "🥣 Producción (Tandas)":
    st.header("Registrar Nueva Tanda")
    conn = get_connection()
    c = conn.cursor()
    dict_recetas = {r[1]: r[0] for r in c.execute("SELECT id, nombre FROM recetas").fetchall()}
    
    if dict_recetas:
        receta_sel = st.selectbox("¿Qué vas a preparar?", list(dict_recetas.keys()))
        cantidad_preparar = st.number_input("¿Cuántas unidades vas a hacer?", min_value=1, step=1)
        notas = st.text_area("Comentarios opcionales")
        
        if st.button("🚀 Registrar Tanda y Descontar Inventario"):
            receta_id = dict_recetas[receta_sel]
            ingredientes = c.execute("SELECT insumo_id, cantidad FROM receta_ingredientes WHERE receta_id=?", (receta_id,)).fetchall()
            
            suficiente = True
            costo_total = 0.0
            for ing_id, cant in ingredientes:
                cant_total = cant * cantidad_preparar
                stock_actual, costo_u, nombre = c.execute("SELECT stock, costo_unidad, nombre FROM insumos WHERE id=?", (ing_id,)).fetchone()
                if stock_actual < cant_total:
                    st.error(f"❌ No hay suficiente {nombre}.")
                    suficiente = False
                    break
                costo_total += (cant_total * costo_u)
                
            if suficiente:
                for ing_id, cant in ingredientes:
                    c.execute("UPDATE insumos SET stock = stock - ? WHERE id=?", (cant * cantidad_preparar, ing_id))
                c.execute("INSERT INTO tandas (receta_id, cantidad_producida, stock_disponible, costo_total, fecha, notas) VALUES (?, ?, ?, ?, ?, ?)", (receta_id, cantidad_preparar, cantidad_preparar, costo_total, datetime.now().strftime("%Y-%m-%d %H:%M"), notas))
                conn.commit()
                st.success(f"✅ Tanda registrada. Costo de producción: ${costo_total:.2f}")
    conn.close()

elif menu == "💰 Ventas":
    st.header("Punto de Venta")
    conn = get_connection()
    df_tandas = pd.read_sql_query('SELECT t.id, r.nombre, t.stock_disponible, r.precio_venta FROM tandas t JOIN recetas r ON t.receta_id = r.id WHERE t.stock_disponible > 0', conn)
    
    if not df_tandas.empty:
        st.dataframe(df_tandas, use_container_width=True)
        tanda_sel = st.selectbox("ID de la Tanda a vender", df_tandas['id'].tolist())
        datos = df_tandas[df_tandas['id'] == tanda_sel].iloc[0]
        cant_vender = st.number_input(f"Cantidad a vender de {datos['nombre']}", min_value=1, max_value=int(datos['stock_disponible']), step=1)
        
        if st.button("💸 Registrar Venta"):
            ingreso = cant_vender * datos['precio_venta']
            c = conn.cursor()
            c.execute("UPDATE tandas SET stock_disponible = stock_disponible - ? WHERE id=?", (cant_vender, tanda_sel))
            c.execute("INSERT INTO finanzas (tipo, monto, fecha, descripcion) VALUES (?, ?, ?, ?)", ("Ingreso", ingreso, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Venta de {cant_vender} {datos['nombre']}"))
            conn.commit()
            st.success(f"✅ Venta registrada. Ganancia: ${ingreso:.2f}")
            st.rerun()
    conn.close()

elif menu == "📊 Finanzas":
    st.header("Resumen Financiero")
    conn = get_connection()
    df_finanzas = pd.read_sql_query("SELECT * FROM finanzas ORDER BY fecha DESC", conn)
    
    if not df_finanzas.empty:
        ingresos = df_finanzas[df_finanzas['tipo'] == 'Ingreso']['monto'].sum()
        egresos = df_finanzas[df_finanzas['tipo'] == 'Egreso']['monto'].sum()
        col1, col2, col3 = st.columns(3)
        col1.metric("Ingresos (Ventas)", f"${ingresos:.2f}")
        col2.metric("Egresos (Insumos)", f"${egresos:.2f}")
        col3.metric("Balance General", f"${ingresos - egresos:.2f}")
        st.dataframe(df_finanzas[['fecha', 'tipo', 'descripcion', 'monto']], use_container_width=True)
    conn.close()
