import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# CONEXIÓN A GOOGLE SHEETS
# ==========================================
@st.cache_resource
def get_sheets_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # Lee la llave secreta que guardaste en Streamlit Cloud
    import json
    secretos = json.loads(st.secrets["google_credentials"], strict=False)
    
    creds = ServiceAccountCredentials.from_json_keyfile_dict(secretos, scope)
    client = gspread.authorize(creds)
    
    # Abre tu hoja de cálculo en Google Drive
    sheet = client.open("Inventario_Negocio")
    return sheet

sh = get_sheets_connection()

# Funciones auxiliares para leer y escribir en las pestañas
def leer_tabla(nombre_pestana):
    data = sh.worksheet(nombre_pestana).get_all_records()
    return pd.DataFrame(data)

def escribir_fila(nombre_pestana, fila):
    sh.worksheet(nombre_pestana).append_row(fila)

def actualizar_tabla(nombre_pestana, df):
    worksheet = sh.worksheet(nombre_pestana)
    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())

# ==========================================
# INTERFAZ DE USUARIO (STREAMLIT)
# ==========================================
st.set_page_config(page_title="Gestion de Postres", layout="wide")
st.title("Sistema de Gestion - Postres y Pays (Nube)")

menu = st.sidebar.selectbox("Menu Principal", ["Inventario y Compras", "Recetas", "Produccion (Tandas)", "Ventas", "Finanzas"])

# --- SECCION 1: INVENTARIO ---
if menu == "Inventario y Compras":
    st.header("Gestion de Insumos")
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
                df_insumos = leer_tabla("insumos")
                
                if not df_insumos.empty and "nombre" in df_insumos.columns and nombre in df_insumos["nombre"].values:
                    idx = df_insumos[df_insumos["nombre"] == nombre].index[0]
                    df_insumos.loc[idx, "stock"] = float(df_insumos.loc[idx, "stock"]) + cantidad_comprada
                    df_insumos.loc[idx, "costo_unidad"] = costo
                    df_insumos.loc[idx, "stock_minimo"] = stock_minimo
                    actualizar_tabla("insumos", df_insumos)
                else:
                    nueva_fila = [nombre, unidad, costo, cantidad_comprada, stock_minimo]
                    if df_insumos.empty:
                        # Crear columnas si está vacía
                        sh.worksheet("insumos").update([["nombre", "unidad", "costo_unidad", "stock", "stock_minimo"]])
                    escribir_fila("insumos", nueva_fila)
                
                # Registrar gasto en finanzas
                gasto_total = costo * cantidad_comprada
                fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                df_fin = leer_tabla("finanzas")
                if df_fin.empty:
                    sh.worksheet("finanzas").update([["tipo", "monto", "fecha", "descripcion"]])
                escribir_fila("finanzas", ["Egreso", gasto_total, fecha_actual, f"Compra de {cantidad_comprada} {unidad} de {nombre}"])
                
                st.success(f"Insumo {nombre} guardado con exito en Google Sheets.")
    
    with col2:
        st.subheader("Inventario Actual en la Nube")
        df_insumos = leer_tabla("insumos")
        if not df_insumos.empty:
            for index, row in df_insumos.iterrows():
                if float(row['stock']) <= float(row['stock_minimo']):
                    st.warning(f"Alerta: Te queda poco/a {row['nombre']} ({row['stock']} {row['unidad']}).")
            st.dataframe(df_insumos, use_container_width=True)
        else:
            st.info("Aun no hay insumos registrados.")

# --- SECCION 2: RECETAS ---
elif menu == "Recetas":
    st.header("Libro de Recetas")
    df_insumos = leer_tabla("insumos")
    nombres_insumos = df_insumos["nombre"].tolist() if not df_insumos.empty and "nombre" in df_insumos.columns else []

    if 'ingredientes_temp' not in st.session_state:
        st.session_state.ingredientes_temp = []

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Crear Nueva Receta")
        nombre_receta = st.text_input("Nombre del producto (ej. Pay de Limon)")
        precio_venta = st.number_input("Precio de venta ($)", min_value=0.0, format="%.2f")
        insumo_sel = st.selectbox("Selecciona un insumo", nombres_insumos if nombres_insumos else ["Vacio"])
        cant_insumo = st.number_input("Cantidad requerida por receta", min_value=0.0)
        
        if st.button("Agregar Ingrediente a la lista"):
            if insumo_sel != "Vacio":
                st.session_state.ingredientes_temp.append({"nombre": insumo_sel, "cantidad": cant_insumo})
                st.success(f"Agregado {cant_insumo} de {insumo_sel}")

        if st.button("Guardar Receta Completa"):
            if nombre_receta and st.session_state.ingredientes_temp:
                df_recetas = leer_tabla("recetas")
                if df_recetas.empty:
                    sh.worksheet("recetas").update([["id", "nombre", "precio_venta"]])
                    nuevo_id = 1
                else:
                    nuevo_id = len(df_recetas) + 1
                
                escribir_fila("recetas", [nuevo_id, nombre_receta, precio_venta])
                
                df_ri = leer_tabla("receta_ingredientes")
                if df_ri.empty:
                    sh.worksheet("receta_ingredientes").update([["receta_id", "insumo_nombre", "cantidad"]])
                
                for ing in st.session_state.ingredientes_temp:
                    escribir_fila("receta_ingredientes", [nuevo_id, ing['nombre'], ing['cantidad']])
                
                st.session_state.ingredientes_temp = []
                st.success("Receta guardada en la nube con exito.")
    
    with col2:
        st.subheader("Ingredientes en esta receta:")
        if st.session_state.ingredientes_temp:
            st.table(pd.DataFrame(st.session_state.ingredientes_temp))
            if st.button("Limpiar Lista"):
                st.session_state.ingredientes_temp = []
                st.rerun()

# --- SECCION 3: PRODUCCION ---
elif menu == "Produccion (Tandas)":
    st.header("Registrar Nueva Tanda")
    df_recetas = leer_tabla("recetas")
    
    if not df_recetas.empty:
        receta_sel = st.selectbox("Que vas a preparar?", df_recetas["nombre"].tolist())
        cantidad_preparar = st.number_input("Cuantas unidades vas a hacer?", min_value=1, step=1)
        notas = st.text_area("Comentarios opcionales")
        
        if st.button("Registrar Tanda y Descontar Inventario"):
            receta_row = df_recetas[df_recetas["nombre"] == receta_sel].iloc[0]
            receta_id = receta_row["id"]
            
            df_ri = leer_tabla("receta_ingredientes")
            ingredientes_receta = df_ri[df_ri["receta_id"] == receta_id]
            df_insumos = leer_tabla("insumos")
            
            suficiente = True
            costo_total = 0.0
            
            for index, row in ingredientes_receta.iterrows():
                insumo_nombre = row["insumo_nombre"]
                cant_necesaria = float(row["cantidad"]) * cantidad_preparar
                
                insumo_row = df_insumos[df_insumos["nombre"] == insumo_nombre].iloc[0]
                stock_actual = float(insumo_row["stock"])
                costo_u = float(insumo_row["costo_unidad"])
                
                if stock_actual < cant_necesaria:
                    st.error(f"Error: No hay suficiente {insumo_nombre}. Tienes {stock_actual} y necesitas {cant_necesaria}.")
                    suficiente = False
                    break
                costo_total += (cant_necesaria * costo_u)
            
            if suficiente:
                # Descontar del inventario
                for index, row in ingredientes_receta.iterrows():
                    insumo_nombre = row["insumo_nombre"]
                    cant_necesaria = float(row["cantidad"]) * cantidad_preparar
                    
                    idx_ins = df_insumos[df_insumos["nombre"] == insumo_nombre].index[0]
                    df_insumos.loc[idx_ins, "stock"] = float(df_insumos.loc[idx_ins, "stock"]) - cant_necesaria
                
                actualizar_tabla("insumos", df_insumos)
                
                # Registrar tanda
                df_tandas = leer_tabla("tandas")
                if df_tandas.empty:
                    sh.worksheet("tandas").update([["id", "receta_nombre", "cantidad_producida", "stock_disponible", "costo_total", "fecha", "notas"]])
                    tanda_id = 1
                else:
                    tanda_id = len(df_tandas) + 1
                
                fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")
                escribir_fila("tandas", [tanda_id, receta_sel, cantidad_preparar, cantidad_preparar, costo_total, fecha_actual, notas])
                st.success(f"Tanda registrada. Costo de produccion: ${costo_total:.2f}")
    else:
        st.info("Primero debes crear recetas.")

# --- SECCION 4: VENTAS ---
elif menu == "Ventas":
    st.header("Punto de Venta")
    df_tandas = leer_tabla("tandas")
    
    if not df_tandas.empty and "stock_disponible" in df_tandas.columns:
        df_disponibles = df_tandas[df_tandas["stock_disponible"].astype(float) > 0]
        
        if not df_disponibles.empty:
            st.dataframe(df_disponibles, use_container_width=True)
            tanda_id_sel = st.selectbox("ID de la Tanda a vender", df_disponibles["id"].tolist())
            
            tanda_row = df_disponibles[df_disponibles["id"] == tanda_id_sel].iloc[0]
            max_stock = int(float(tanda_row["stock_disponible"]))
            
            cant_vender = st.number_input(f"Cantidad a vender de {tanda_row['receta_nombre']}", min_value=1, max_value=max_stock, step=1)
            
            if st.button("Registrar Venta"):
                df_recetas = leer_tabla("recetas")
                precio_v = float(df_recetas[df_recetas["nombre"] == tanda_row["receta_nombre"]].iloc[0]["precio_venta"])
                ingreso = cant_vender * precio_v
                
                # Actualizar stock tanda
                idx_tanda = df_tandas[df_tandas["id"] == tanda_id_sel].index[0]
                df_tandas.loc[idx_tanda, "stock_disponible"] = float(df_tandas.loc[idx_tanda, "stock_disponible"]) - cant_vender
                actualizar_tabla("tandas", df_tandas)
                
                # Registrar ingreso en finanzas
                fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                escribir_fila("finanzas", ["Ingreso", ingreso, fecha_actual, f"Venta de {cant_vender} {tanda_row['receta_nombre']}"])
                
                st.success(f"Venta registrada. Ganancia: ${ingreso:.2f}")
                st.rerun()
        else:
            st.info("No hay productos terminados disponibles para la venta.")
    else:
        st.info("No hay tandas registradas todavia.")

# --- SECCION 5: FINANZAS ---
elif menu == "Finanzas":
    st.header("Resumen Financiero")
    df_finanzas = leer_tabla("finanzas")
    
    if not df_finanzas.empty:
        ingresos = df_finanzas[df_finanzas['tipo'] == 'Ingreso']['monto'].astype(float).sum()
        egresos = df_finanzas[df_finanzas['tipo'] == 'Egreso']['monto'].astype(float).sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Ingresos (Ventas)", f"${ingresos:.2f}")
        col2.metric("Egresos (Insumos)", f"${egresos:.2f}")
        col3.metric("Balance General", f"${ingresos - egresos:.2f}")
        
        st.dataframe(df_finanzas, use_container_width=True)
    else:
        st.info("Aun no hay movimientos financieros.")
