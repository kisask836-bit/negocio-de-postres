# -*- coding: utf-8 -*-
"""
💎 Lady Pays – Insumos, recetas, producción, ventas y finanzas

Secrets (.streamlit/secrets.toml o panel de Streamlit Cloud):
    google_credentials = "<JSON de la cuenta de servicio>"
    sheet_id           = "<ID de la hoja>"      # opcional pero recomendado (más rápido que abrir por nombre)
    app_password       = "tu_contraseña"        # opcional

Cómo se protegen los datos
--------------------------
* Cada acción (vender, producir, comprar, merma, anular...) se manda a Google como UNA sola
  petición atómica: o se guarda todo o no se guarda nada.
* Antes de guardar se vuelven a leer los datos frescos de Google, así que no se pisan los
  cambios que hiciste a mano en la hoja, y solo se escriben las celdas que cambian.
* Una receta indica cuántas piezas rinde cada tanda; el costo por pieza sale de ahí.
* Cada acción lleva un código único: si reintentas o tocas dos veces, la app detecta que ya
  quedó guardada y no la duplica.
"""
import hashlib
import hmac
import json
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import gspread
import pandas as pd
import requests
import streamlit as st

NOMBRE_HOJA = "Inventario_Negocio"   # solo se usa si no defines `sheet_id` en los secrets

st.set_page_config(page_title="Lady Pays", layout="centered", page_icon="💎")

# Estilos neutros: funcionan igual en modo claro y oscuro (sin colores de fondo forzados).
st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 3rem; }
    .stMetric { background: rgba(128,128,128,.08); padding: 10px 14px; border-radius: 10px;
                border: 1px solid rgba(128,128,128,.25); }
    .stButton button, .stDownloadButton button, [data-testid="stFormSubmitButton"] button
        { min-height: 3rem; font-weight: 600; }
    div[data-testid="stNumberInput"] input, div[data-testid="stTextInput"] input { font-size: 1.05rem; }
</style>
""", unsafe_allow_html=True)


# >>> NUCLEO (lógica sin Streamlit: se puede probar por separado) >>>
# ==========================================
# UTILIDADES
# ==========================================
try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Mexico_City")
except Exception:
    ZONA = timezone(timedelta(hours=-6))


def ahora():
    return datetime.now(ZONA)


def ahora_str():
    return ahora().strftime("%Y-%m-%d %H:%M:%S")


def r4(x):
    """Redondea a 4 decimales (evita 9.700000000000001) y quita el -0.0."""
    return round(float(x), 4) + 0.0


def r2(x):
    return round(float(x), 2) + 0.0


def cant(x):
    """Cantidad lista para mostrar: 9.7, 250, 0.5 (sin ceros ni ruido)."""
    return f"{r4(x):g}"


def dinero(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "$0.00"


def _py(v):
    """Convierte tipos de numpy/pandas a tipos simples y NaN a vacío."""
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, float) and v != v:
        return ""
    return v


# ==========================================
# ESQUEMA DE LA BASE (tipos: t texto, n número, i entero, r tal cual)
# ==========================================
ESQUEMA = {
    "insumos": {"nombre": "t", "categoria": "t", "unidad": "t", "costo_unidad": "n",
                "stock": "n", "stock_minimo": "n"},
    "recetas": {"id": "i", "nombre": "t", "categoria": "t", "precio_venta": "n", "vida_util_dias": "n",
                "rinde": "n"},
    "receta_ingredientes": {"receta_id": "i", "insumo_nombre": "t", "cantidad": "n"},
    "tandas": {"id": "i", "receta_nombre": "t", "cantidad_producida": "n", "stock_disponible": "n",
               "costo_total": "n", "fecha": "t", "notas": "t", "caduca": "t", "ref": "t"},
    "finanzas": {"id": "i", "tipo": "t", "monto": "n", "fecha": "t", "descripcion": "t",
                 "categoria": "t", "ref": "t"},
    "mermas": {"id": "i", "tipo": "t", "nombre": "t", "cantidad": "n", "unidad": "t",
               "costo_estimado": "n", "fecha": "t", "motivo": "t", "detalle": "t", "ref": "t"},
    "ventas": {"id": "i", "fecha": "t", "producto": "t", "cantidad": "n", "precio_unit": "n",
               "total": "n", "costo": "n", "lotes": "t", "nota": "t", "estado": "t", "ref": "t"},
    "config": {"clave": "t", "valor": "r"},
}
COLS_FECHA = {"fecha", "caduca"}

CATEGORIAS_BASE = ["LÁCTEOS", "SECOS", "FRUTAS", "EMPAQUES", "OTROS"]
UNIDADES_BASE = ["latas", "paquetes", "g", "ml", "piezas", "Kg", "Litro"]
UNIDADES_CONTEO = {"latas", "lata", "paquetes", "paquete", "piezas", "pieza", "pza", "pzas"}
MOTIVOS_MERMA = ["Se echó a perder", "Se cayó / se rompió", "Error de preparación", "Caducó",
                 "Regalo / cortesía", "Otro"]
CATEGORIAS_GASTO = ["Renta", "Luz / Gas / Agua", "Transporte", "Publicidad", "Sueldos",
                    "Equipo y utensilios", "Otro"]


class Fallo(Exception):
    """Error de negocio con un mensaje claro para la persona (no se guardó nada)."""


def opciones(base, extras):
    vistos, salida = set(), []
    for x in list(base) + sorted({str(e).strip() for e in extras if str(e).strip()}):
        if x not in vistos:
            vistos.add(x)
            salida.append(x)
    return salida


def paso_para(unidad):
    u = str(unidad).strip().lower()
    return 1.0 if (u in UNIDADES_CONTEO or u in ("g", "ml", "gr")) else 0.1


# ---------- conversión de unidades al comprar (kg↔g, litro↔ml) ----------
_MASA = {"g": 1.0, "gr": 1.0, "kg": 1000.0}
_VOL = {"ml": 1.0, "l": 1000.0, "lt": 1000.0, "litro": 1000.0, "litros": 1000.0}


def conversiones(unidad):
    """Lista [(etiqueta, factor)] para comprar en otra unidad. factor = cuántas unidades
    del inventario equivale 1 de la etiqueta. Ej. inventario en g: [('g',1), ('Kg',1000)]."""
    u = str(unidad).strip().lower()
    if u in _MASA:
        base, f, cand = _MASA, _MASA[u], [("g", 1.0), ("Kg", 1000.0)]
    elif u in _VOL:
        base, f, cand = _VOL, _VOL[u], [("ml", 1.0), ("Litro", 1000.0)]
    else:
        return [(unidad, 1.0)]
    res = [(unidad, 1.0)]
    for etiqueta, valor in cand:
        factor = valor / f
        if abs(factor - 1.0) > 1e-9:
            res.append((etiqueta, factor))
    return res


def rinde_de(x):
    """Piezas que rinde una tanda. Las recetas viejas (sin dato) cuentan como 1."""
    try:
        v = int(round(float(x)))
    except Exception:
        return 1
    return v if v >= 1 else 1


def precio_sugerido(costo, margen_pct):
    """Precio para ganar `margen_pct`% sobre el precio de venta, redondeado hacia arriba al peso."""
    m = min(max(float(margen_pct), 0.0), 95.0) / 100.0
    return float(math.ceil(float(costo) / (1.0 - m) - 1e-9))


def redondeo_compra(x, unidad):
    if str(unidad).strip().lower() in UNIDADES_CONTEO:
        return float(math.ceil(x - 1e-9))
    return math.ceil(x * 100 - 1e-9) / 100.0


def dias_para_caducar(caduca):
    try:
        return (datetime.strptime(str(caduca)[:10], "%Y-%m-%d").date() - ahora().date()).days
    except Exception:
        return None


# ==========================================
# LECTURA Y NORMALIZACIÓN
# ==========================================
def _fecha_txt(v):
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v > 20000:  # número de serie de fecha de Google Sheets (si alguien tecleó una fecha a mano)
            return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d %H:%M:%S")
        return str(v) if v else ""
    return str(v).strip()


def _vacia(nombre, con_fila=False):
    tipos = {"t": "object", "n": "float64", "i": "int64", "r": "object"}
    cols = {c: pd.Series(dtype=tipos[t]) for c, t in ESQUEMA[nombre].items()}
    if con_fila:
        cols["_fila"] = pd.Series(dtype="int64")
    return pd.DataFrame(cols)


def _a_df(filas):
    """Convierte lo que devuelve Google (lista de listas) en DataFrame + encabezado real.
    Guarda el número de fila de la hoja en `_fila` para poder editar celdas exactas."""
    if not filas:
        return pd.DataFrame(), []
    cab = [str(h).strip() for h in filas[0]]
    ancho = len(cab)
    cuerpo = [(list(f) + [""] * ancho)[:ancho] for f in filas[1:]]
    df = pd.DataFrame(cuerpo, columns=cab)
    df["_fila"] = list(range(2, 2 + len(df)))
    return df, cab


def _normalizar(df, nombre, con_fila=False):
    """Garantiza columnas y tipos (evita KeyError y mezclas texto/número)."""
    esq = ESQUEMA[nombre]
    cols = list(esq)
    if df is None or df.empty:
        return _vacia(nombre, con_fila)
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    texto = df[cols].astype(str).apply(lambda s: s.str.strip())
    df = df[~texto.eq("").all(axis=1)].copy()
    for c, t in esq.items():
        if t == "t":
            if c in COLS_FECHA:
                df[c] = df[c].map(_fecha_txt)
            else:
                df[c] = df[c].fillna("").astype(str).str.strip()
        elif t == "n":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype(float).round(4)
        elif t == "i":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    extra = ["_fila"] if con_fila else []
    return df[cols + extra].reset_index(drop=True)


# ==========================================
# OPERACIÓN ATÓMICA
# ==========================================
def _celda(v):
    v = _py(v)
    if v is None or (isinstance(v, str) and v == ""):
        return {}
    if isinstance(v, bool):
        return {"userEnteredValue": {"boolValue": v}}
    if isinstance(v, (int, float)):
        return {"userEnteredValue": {"numberValue": v}}
    return {"userEnteredValue": {"stringValue": str(v)}}


class Op:
    """Junta todos los cambios de UNA acción y los convierte en una sola petición a Google.
    Trabaja sobre datos FRESCOS (`T`) y solo toca las celdas que cambian."""

    def __init__(self, T, H, token):
        self.T, self.H, self.token = T, H, token
        self.mensaje = ""
        self.ids = {}
        self._val = {}     # (tabla, fila, columna) -> valor nuevo
        self._altas = []   # (tabla, {columna: valor})
        self._bajas = {}   # tabla -> {filas}
        self._sig = {}     # tabla -> siguiente id

    # ---- consulta ----
    def buscar(self, tabla, col, valor):
        df = self.T[tabla]
        m = df[df[col] == valor]
        m = m[~m["_fila"].isin(self._bajas.get(tabla, ()))]
        return None if m.empty else m.iloc[0]

    def requerir(self, tabla, col, valor, que="El registro"):
        r = self.buscar(tabla, col, valor)
        if r is None:
            raise Fallo(f"{que} ya no existe (¿lo borraste o lo cambiaste en la hoja?). Toca 🔄 para actualizar.")
        return r

    def valor(self, tabla, serie, col):
        return self._val.get((tabla, int(serie["_fila"]), col), serie[col])

    # ---- cambios ----
    def fijar(self, tabla, col, valor, campos, que="El registro"):
        r = self.requerir(tabla, col, valor, que)
        for c, v in campos.items():
            self._val[(tabla, int(r["_fila"]), c)] = v

    def fijar_donde(self, tabla, col, valor, campos):
        df = self.T[tabla]
        for f in df.loc[df[col] == valor, "_fila"]:
            for c, v in campos.items():
                self._val[(tabla, int(f), c)] = v

    def sumar(self, tabla, col, valor, campo, delta, minimo=None, que=None):
        r = self.requerir(tabla, col, valor, que or f"«{valor}»")
        k = (tabla, int(r["_fila"]), campo)
        actual = float(self._val.get(k, r[campo]))
        nuevo = r4(actual + delta)
        if minimo is not None and nuevo < minimo - 1e-9:
            raise Fallo(f"No alcanza {que or '«' + str(valor) + '»'}: hay {cant(actual)} y se necesitan {cant(-delta)}.")
        self._val[k] = nuevo
        return nuevo

    def borrar(self, tabla, col, valor, que="El registro"):
        r = self.requerir(tabla, col, valor, que)
        self._bajas.setdefault(tabla, set()).add(int(r["_fila"]))

    def borrar_donde(self, tabla, col, valor):
        df = self.T[tabla]
        filas = [int(f) for f in df.loc[df[col] == valor, "_fila"]]
        self._bajas.setdefault(tabla, set()).update(filas)
        return len(filas)

    def sig_id(self, tabla):
        """Siguiente id. Usa un contador guardado en `config`, así un número de lote
        o de venta borrado NUNCA se vuelve a usar."""
        if tabla not in self._sig:
            cfg = self.T["config"]
            m = cfg[cfg["clave"] == f"sig_{tabla}"]
            guardado = 1
            if not m.empty:
                x = pd.to_numeric(pd.Series([m.iloc[0]["valor"]]), errors="coerce").iloc[0]
                guardado = int(x) if x == x else 1
            df = self.T[tabla]
            maximo = int(df["id"].max()) if not df.empty else 0
            self._sig[tabla] = max(guardado, maximo + 1, 1)
        n = self._sig[tabla]
        self._sig[tabla] = n + 1
        return n

    def agregar(self, tabla, **campos):
        esq = ESQUEMA[tabla]
        if "id" in esq and "id" not in campos:
            campos["id"] = self.sig_id(tabla)
            self.ids.setdefault(tabla, []).append(campos["id"])
        if "ref" in esq:
            campos.setdefault("ref", self.token)
        self._altas.append((tabla, campos))
        return campos.get("id")

    # ---- construcción de la petición ----
    def _col(self, tabla, col):
        try:
            return self.H[tabla].index(col)
        except ValueError:
            raise Fallo(f"A la pestaña «{tabla}» le falta la columna «{col}». Recarga la app para repararla.")

    def requests(self, ids_hoja):
        cfg = self.T["config"]
        for tabla, sig in self._sig.items():  # guardar contadores
            m = cfg[cfg["clave"] == f"sig_{tabla}"]
            if m.empty:
                self._altas.append(("config", {"clave": f"sig_{tabla}", "valor": sig}))
            else:
                self._val[("config", int(m.iloc[0]["_fila"]), "valor")] = sig
        reqs = []
        for (tabla, fila, col), v in self._val.items():
            if fila in self._bajas.get(tabla, ()):
                continue
            reqs.append({"updateCells": {
                "rows": [{"values": [_celda(v)]}], "fields": "userEnteredValue",
                "start": {"sheetId": ids_hoja[tabla], "rowIndex": fila - 1,
                          "columnIndex": self._col(tabla, col)}}})
        for tabla, filas in self._bajas.items():
            for f in sorted(filas, reverse=True):
                if f <= 1:
                    continue
                reqs.append({"deleteDimension": {"range": {
                    "sheetId": ids_hoja[tabla], "dimension": "ROWS", "startIndex": f - 1, "endIndex": f}}})
        por_tabla = {}
        for tabla, campos in self._altas:
            por_tabla.setdefault(tabla, []).append(campos)
        for tabla, filas in por_tabla.items():
            filas_g = []
            for campos in filas:
                for c in campos:
                    self._col(tabla, c)
                filas_g.append({"values": [_celda(campos.get(c, "")) for c in self.H[tabla]]})
            reqs.append({"appendCells": {"sheetId": ids_hoja[tabla], "rows": filas_g,
                                         "fields": "userEnteredValue"}})
        return reqs


def agregar_config(op, clave, valor):
    cfg = op.T["config"]
    if not ((cfg["clave"] == clave) & (cfg["valor"].astype(str) == str(valor))).any():
        op.agregar("config", clave=clave, valor=valor)


def descontar_fifo(op, producto, cantidad):
    """Descuenta `cantidad` del producto tomando primero el lote que caduca antes
    (y si no caducan, el más viejo). Devuelve (texto de lotes, costo)."""
    t = op.T["tandas"]
    lotes = t[(t["receta_nombre"] == producto) & (t["stock_disponible"] > 1e-9)].copy()
    lotes["_k"] = lotes["caduca"].where(lotes["caduca"] != "", "9999-12-31").str[:10]
    lotes = lotes.sort_values(["_k", "id"])
    faltan, partes, costo = r4(cantidad), [], 0.0
    for _, lote in lotes.iterrows():
        if faltan <= 1e-9:
            break
        toma = min(faltan, float(op.valor("tandas", lote, "stock_disponible")))
        if toma <= 1e-9:
            continue
        op.sumar("tandas", "id", int(lote["id"]), "stock_disponible", -toma, minimo=0)
        prod = float(lote["cantidad_producida"])
        costo += toma * (float(lote["costo_total"]) / prod if prod > 0 else 0.0)
        partes.append(f"{int(lote['id'])}:{cant(toma)}")
        faltan = r4(faltan - toma)
    if faltan > 1e-9:
        raise Fallo(f"Solo quedan {cant(cantidad - faltan)} de {producto} y pediste {cant(cantidad)}.")
    return "|".join(partes), r2(costo)


# ---------- acciones de negocio (cada una = UNA operación atómica) ----------
def b_vender(producto, cantidad, precio, nota):
    def f(op):
        lotes, costo = descontar_fifo(op, producto, cantidad)
        total = r2(cantidad * precio)
        vid = op.agregar("ventas", fecha=ahora_str(), producto=producto, cantidad=cantidad,
                         precio_unit=r2(precio), total=total, costo=costo, lotes=lotes,
                         nota=nota, estado="Activa")
        desc = f"Venta de {cant(cantidad)} {producto}" + (f" - {nota}" if nota else "")
        op.agregar("finanzas", tipo="Ingreso", monto=total, fecha=ahora_str(),
                   descripcion=desc, categoria="Ventas")
        op.mensaje = f"✅ Venta #{vid} registrada: {dinero(total)}"
    return f


def b_anular_venta(vid):
    def f(op):
        v = op.requerir("ventas", "id", vid, "La venta")
        if v["estado"] == "Anulada":
            raise Fallo("Esa venta ya estaba anulada.")
        perdidos = 0
        for parte in str(v["lotes"]).split("|"):
            if ":" not in parte:
                continue
            lid, c = parte.split(":")
            if op.buscar("tandas", "id", int(lid)) is None:
                perdidos += 1
                continue
            op.sumar("tandas", "id", int(lid), "stock_disponible", float(c))
        op.fijar("ventas", "id", vid, {"estado": "Anulada"})
        if v["ref"]:
            op.borrar_donde("finanzas", "ref", v["ref"])
        op.mensaje = f"↩️ Venta #{vid} anulada: se devolvieron las piezas al lote y se quitó el ingreso."
        if perdidos:
            op.mensaje += f" (⚠️ {perdidos} lote(s) ya no existían; esas piezas no se pudieron devolver.)"
    return f


def b_producir(receta, n, notas):
    def f(op):
        rec = op.requerir("recetas", "nombre", receta, "La receta")
        ri = op.T["receta_ingredientes"]
        ing = ri[ri["receta_id"] == int(rec["id"])]
        if ing.empty:
            raise Fallo("La receta no tiene ingredientes.")
        costo = 0.0
        for _, i in ing.iterrows():
            need = r4(float(i["cantidad"]) * n)
            ins = op.requerir("insumos", "nombre", i["insumo_nombre"], f"El insumo «{i['insumo_nombre']}»")
            costo += need * float(ins["costo_unidad"])
            op.sumar("insumos", "nombre", i["insumo_nombre"], "stock", -need, minimo=0,
                     que=f"«{i['insumo_nombre']}»")
        vida = float(rec["vida_util_dias"])
        caduca = (ahora() + timedelta(days=vida)).strftime("%Y-%m-%d") if vida > 0 else ""
        piezas = n * rinde_de(rec["rinde"])
        tid = op.agregar("tandas", receta_nombre=receta, cantidad_producida=piezas, stock_disponible=piezas,
                         costo_total=r2(costo), fecha=ahora().strftime("%Y-%m-%d %H:%M"),
                         notas=notas, caduca=caduca)
        op.mensaje = (f"✅ Lote #{tid}: {n} tanda(s) = {piezas} × {receta}. "
                      f"Costo real por pieza: {dinero(costo / piezas)}.")
    return f


def b_corregir_lote(tid, producidas, disp, notas, costo):
    def f(op):
        if producidas < 1:
            raise Fallo("Las piezas producidas deben ser al menos 1.")
        if disp > producidas + 1e-9:
            raise Fallo("No puede haber más piezas disponibles que producidas.")
        op.fijar("tandas", "id", tid, {"cantidad_producida": r4(producidas), "stock_disponible": r4(disp),
                                       "notas": notas, "costo_total": r2(costo)}, "El lote")
        op.mensaje = "✅ Lote actualizado."
    return f


def b_borrar_lote(tid, devolver):
    def f(op):
        t = op.requerir("tandas", "id", tid, "El lote")
        if devolver:
            rec = op.buscar("recetas", "nombre", t["receta_nombre"])
            if rec is None:
                raise Fallo("La receta de este lote ya no existe: no puedo devolver los insumos. Borra sin devolver.")
            ri = op.T["receta_ingredientes"]
            for _, i in ri[ri["receta_id"] == int(rec["id"])].iterrows():
                if op.buscar("insumos", "nombre", i["insumo_nombre"]) is not None:
                    op.sumar("insumos", "nombre", i["insumo_nombre"], "stock",
                             float(i["cantidad"]) * float(t["cantidad_producida"]))
        op.borrar("tandas", "id", tid, "El lote")
        op.mensaje = "🗑️ Lote borrado." + (" Insumos devueltos al inventario." if devolver else "")
    return f


def b_comprar(nombre, cant_inv, total, minimo):
    def f(op):
        ins = op.requerir("insumos", "nombre", nombre, f"El insumo «{nombre}»")
        op.sumar("insumos", "nombre", nombre, "stock", cant_inv)
        op.fijar("insumos", "nombre", nombre, {"costo_unidad": r4(total / cant_inv), "stock_minimo": r4(minimo)})
        op.agregar("finanzas", tipo="Egreso", monto=r2(total), fecha=ahora_str(),
                   descripcion=f"Compra de {cant(cant_inv)} {ins['unidad']} de {nombre}",
                   categoria=f"Insumos: {ins['categoria']}")
        op.mensaje = f"✅ Compra registrada: +{cant(cant_inv)} {ins['unidad']} de {nombre} ({dinero(total)})."
    return f


def b_nuevo_insumo(nombre, cat, uni, costo, stock, minimo, gasto, cat_nueva, uni_nueva):
    def f(op):
        if op.T["insumos"]["nombre"].str.lower().eq(nombre.lower()).any():
            raise Fallo(f"«{nombre}» ya existe. Usa **Comprar / reabastecer** para sumarle stock.")
        op.agregar("insumos", nombre=nombre, categoria=cat, unidad=uni, costo_unidad=r4(costo),
                   stock=r4(stock), stock_minimo=r4(minimo))
        if cat_nueva:
            agregar_config(op, "cat_insumo", cat)
        if uni_nueva:
            agregar_config(op, "unidad_insumo", uni)
        if gasto and stock > 0 and costo > 0:
            op.agregar("finanzas", tipo="Egreso", monto=r2(costo * stock), fecha=ahora_str(),
                       descripcion=f"Compra de {cant(stock)} {uni} de {nombre}", categoria=f"Insumos: {cat}")
        op.mensaje = f"✅ Insumo «{nombre}» guardado."
    return f


def b_editar_insumo(viejo, nombre, cat, uni, costo, stock, minimo, cat_nueva, uni_nueva):
    def f(op):
        op.requerir("insumos", "nombre", viejo, f"El insumo «{viejo}»")
        if nombre.lower() != viejo.lower() and op.T["insumos"]["nombre"].str.lower().eq(nombre.lower()).any():
            raise Fallo(f"Ya existe otro insumo llamado «{nombre}».")
        op.fijar("insumos", "nombre", viejo, {"nombre": nombre, "categoria": cat, "unidad": uni,
                                              "costo_unidad": r4(costo), "stock": r4(stock),
                                              "stock_minimo": r4(minimo)})
        if nombre != viejo:
            op.fijar_donde("receta_ingredientes", "insumo_nombre", viejo, {"insumo_nombre": nombre})
        if cat_nueva:
            agregar_config(op, "cat_insumo", cat)
        if uni_nueva:
            agregar_config(op, "unidad_insumo", uni)
        op.mensaje = "✅ Insumo actualizado."
    return f


def b_borrar_insumo(nombre):
    def f(op):
        ri, rec = op.T["receta_ingredientes"], op.T["recetas"]
        usos = ri[ri["insumo_nombre"] == nombre]
        if not usos.empty:
            nombres = rec[rec["id"].isin(usos["receta_id"])]["nombre"].tolist()
            raise Fallo(f"«{nombre}» se usa en: {', '.join(nombres) or 'una receta'}. Quítalo primero de esas recetas.")
        op.borrar("insumos", "nombre", nombre, f"El insumo «{nombre}»")
        op.mensaje = f"🗑️ Insumo «{nombre}» eliminado."
    return f


def b_crear_receta(nombre, cat, precio, vida, rinde, ingredientes):
    def f(op):
        if op.T["recetas"]["nombre"].str.lower().eq(nombre.lower()).any():
            raise Fallo("Ya existe una receta con ese nombre.")
        for ins, _ in ingredientes:
            op.requerir("insumos", "nombre", ins, f"El insumo «{ins}»")
        rid = op.agregar("recetas", nombre=nombre, categoria=cat, precio_venta=r2(precio), vida_util_dias=vida,
                         rinde=rinde_de(rinde))
        for ins, c in ingredientes:
            op.agregar("receta_ingredientes", receta_id=rid, insumo_nombre=ins, cantidad=r4(c))
        op.mensaje = f"✅ Receta «{nombre}» guardada."
    return f


def b_editar_receta(rid, nombre, cat, precio, vida, rinde):
    def f(op):
        r = op.requerir("recetas", "id", rid, "La receta")
        if nombre.lower() != r["nombre"].lower() and op.T["recetas"]["nombre"].str.lower().eq(nombre.lower()).any():
            raise Fallo("Ya existe otra receta con ese nombre.")
        op.fijar("recetas", "id", rid, {"nombre": nombre, "categoria": cat, "precio_venta": r2(precio),
                                        "vida_util_dias": vida, "rinde": rinde_de(rinde)})
        if nombre != r["nombre"]:
            op.fijar_donde("tandas", "receta_nombre", r["nombre"], {"receta_nombre": nombre})
            op.fijar_donde("ventas", "producto", r["nombre"], {"producto": nombre})
        op.mensaje = "✅ Receta actualizada."
    return f


def b_precio_receta(rid, precio):
    def f(op):
        op.fijar("recetas", "id", rid, {"precio_venta": r2(precio)}, "La receta")
        op.mensaje = f"✅ Precio actualizado a {dinero(precio)}."
    return f


def b_ingredientes_receta(rid, ingredientes):
    def f(op):
        op.requerir("recetas", "id", rid, "La receta")
        for ins, _ in ingredientes:
            op.requerir("insumos", "nombre", ins, f"El insumo «{ins}»")
        op.borrar_donde("receta_ingredientes", "receta_id", rid)
        for ins, c in ingredientes:
            op.agregar("receta_ingredientes", receta_id=rid, insumo_nombre=ins, cantidad=r4(c))
        op.mensaje = "✅ Ingredientes actualizados."
    return f


def b_borrar_receta(rid):
    def f(op):
        op.borrar("recetas", "id", rid, "La receta")
        op.borrar_donde("receta_ingredientes", "receta_id", rid)
        op.mensaje = "🗑️ Receta eliminada."
    return f


def b_merma_insumo(nombre, c, motivo, detalle):
    def f(op):
        ins = op.requerir("insumos", "nombre", nombre, f"El insumo «{nombre}»")
        op.sumar("insumos", "nombre", nombre, "stock", -c, minimo=0, que=f"«{nombre}»")
        costo = r2(c * float(ins["costo_unidad"]))
        op.agregar("mermas", tipo="Insumo", nombre=nombre, cantidad=r4(c), unidad=ins["unidad"],
                   costo_estimado=costo, fecha=ahora().strftime("%Y-%m-%d %H:%M"), motivo=motivo, detalle=detalle)
        op.mensaje = f"✅ Merma registrada (pérdida estimada {dinero(costo)})."
    return f


def b_merma_producto(producto, c, motivo, detalle):
    def f(op):
        _, costo = descontar_fifo(op, producto, c)
        op.agregar("mermas", tipo="Producto Terminado", nombre=producto, cantidad=r4(c), unidad="piezas",
                   costo_estimado=costo, fecha=ahora().strftime("%Y-%m-%d %H:%M"), motivo=motivo, detalle=detalle)
        op.mensaje = f"✅ Merma de producto registrada (pérdida estimada {dinero(costo)})."
    return f


def b_editar_merma(mid, c, costo, motivo, detalle):
    def f(op):
        op.fijar("mermas", "id", mid, {"cantidad": r4(c), "costo_estimado": r2(costo), "motivo": motivo,
                                       "detalle": detalle}, "La merma")
        op.mensaje = "✅ Merma actualizada."
    return f


def b_borrar_merma(mid, devolver):
    def f(op):
        m = op.requerir("mermas", "id", mid, "La merma")
        aviso_extra = ""
        if devolver and m["tipo"] == "Insumo":
            if op.buscar("insumos", "nombre", m["nombre"]) is None:
                aviso_extra = " (el insumo ya no existe; no se pudo devolver al inventario)"
            else:
                op.sumar("insumos", "nombre", m["nombre"], "stock", float(m["cantidad"]))
        op.borrar("mermas", "id", mid, "La merma")
        op.mensaje = "🗑️ Merma eliminada." + aviso_extra
    return f


def b_movimiento(tipo, monto, cat, desc):
    def f(op):
        op.agregar("finanzas", tipo=tipo, monto=r2(monto), fecha=ahora_str(), descripcion=desc, categoria=cat)
        op.mensaje = "✅ Movimiento registrado."
    return f


def b_editar_movimiento(fid, tipo, monto, cat, desc):
    def f(op):
        op.fijar("finanzas", "id", fid, {"tipo": tipo, "monto": r2(monto), "categoria": cat,
                                         "descripcion": desc}, "El movimiento")
        op.mensaje = "✅ Movimiento actualizado."
    return f


def b_borrar_movimiento(fid):
    def f(op):
        op.borrar("finanzas", "id", fid, "El movimiento")
        op.mensaje = "🗑️ Movimiento eliminado."
    return f
# <<< NUCLEO <<<


# ==========================================
# ACCESO CON CONTRASEÑA (opcional)
# ==========================================
def _token_acceso(clave):
    return hmac.new(clave.encode(), b"ladypays-acceso-v1", hashlib.sha256).hexdigest()[:32]


def _igual(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def verificar_acceso():
    try:
        clave = str(st.secrets.get("app_password", "") or "")
    except Exception:
        clave = ""
    if not clave or st.session_state.get("acceso_ok"):
        return
    tok = _token_acceso(clave)
    if _igual(st.query_params.get("k", ""), tok):   # dispositivo recordado
        st.session_state["acceso_ok"] = True
        return
    st.title("💎 Lady Pays")
    intento = st.text_input("Contraseña", type="password", key="pw_input")
    recordar = st.checkbox("Recordar en este dispositivo", value=True, key="pw_recordar",
                           help="Guarda un código en la dirección de la página. No compartas esa dirección.")
    if st.button("Entrar", type="primary", key="pw_btn"):
        if _igual(intento, clave):
            st.session_state["acceso_ok"] = True
            if recordar:
                st.query_params["k"] = tok
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    st.stop()


verificar_acceso()


# ==========================================
# GOOGLE SHEETS
# ==========================================
@st.cache_resource(show_spinner="Conectando con Google Sheets...")
def conectar():
    bruto = st.secrets["google_credentials"]
    secretos = json.loads(bruto, strict=False) if isinstance(bruto, str) else dict(bruto)
    cliente = gspread.service_account_from_dict(secretos)
    try:
        sid = str(st.secrets.get("sheet_id", "") or "").strip()
    except Exception:
        sid = ""
    return cliente.open_by_key(sid) if sid else cliente.open(NOMBRE_HOJA)


def _con_reintento(fn, intentos=3):
    """Solo para LECTURAS. Las escrituras nunca se reintentan solas (podrían duplicarse)."""
    for i in range(intentos):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            codigo = getattr(getattr(e, "response", None), "status_code", 0)
            if i < intentos - 1 and codigo in (429, 500, 502, 503):
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except requests.exceptions.RequestException:
            if i < intentos - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise


try:
    sh = conectar()
except Exception as e:
    st.error("❌ No se pudo conectar con Google Sheets. Revisa el secreto `google_credentials`, "
             "que la hoja esté compartida con la cuenta de servicio y, si usas `sheet_id`, que sea correcto.")
    st.code(str(e))
    st.stop()


@st.cache_resource(show_spinner="Preparando la hoja...")
def inicializar():
    """Crea pestañas que falten y agrega columnas nuevas al final (sin tocar tus datos)."""
    existentes = {ws.title: ws for ws in sh.worksheets()}
    nombres = [n for n in ESQUEMA if n in existentes]
    cabeceras = {}
    if nombres:
        r = sh.values_batch_get([f"{n}!1:1" for n in nombres])
        for n, vr in zip(nombres, r.get("valueRanges", [])):
            v = vr.get("values", [[]])
            cabeceras[n] = [str(c).strip() for c in (v[0] if v else [])]
    ids = {}
    for nombre, esq in ESQUEMA.items():
        cols = list(esq)
        if nombre not in existentes:
            ws = sh.add_worksheet(title=nombre, rows=200, cols=max(len(cols), 10))
            ws.update(values=[cols], range_name="A1")
        else:
            ws = existentes[nombre]
            cab = cabeceras.get(nombre, [])
            nuevo = cols if not cab else (cab + [c for c in cols if c not in cab] if any(c not in cab for c in cols) else None)
            if nuevo:
                if len(nuevo) > ws.col_count:
                    ws.resize(cols=len(nuevo))
                ws.update(values=[nuevo], range_name="A1")
        ids[nombre] = ws.id
    return ids


try:
    SHEET_IDS = inicializar()
except Exception as e:
    st.error("❌ No se pudo preparar la hoja de cálculo.")
    st.code(str(e))
    st.stop()


def _leer_crudo():
    r = _con_reintento(lambda: sh.values_batch_get(
        list(ESQUEMA), params={"valueRenderOption": "UNFORMATTED_VALUE"}))
    rangos = r.get("valueRanges", [])
    return {n: (rangos[i].get("values", []) if i < len(rangos) else []) for i, n in enumerate(ESQUEMA)}


@st.cache_data(ttl=120, show_spinner=False)
def _leer_todo():
    """UNA sola llamada a Google para las 8 pestañas. Se guarda 2 minutos."""
    crudo = _leer_crudo()
    return {n: _normalizar(_a_df(crudo[n])[0], n) for n in ESQUEMA}


def cargar_todo():
    try:
        return _leer_todo()
    except Exception as e:
        st.error("⚠️ No pude leer Google Sheets (internet o límite de lecturas por minuto). "
                 "Espera unos segundos y toca **Reintentar**.")
        st.caption(str(e)[:300])
        if st.button("🔄 Reintentar"):
            st.rerun()
        st.stop()


def ejecutar(builder, token):
    """Lee datos frescos → construye la acción → la manda en UNA petición atómica.
    Devuelve (op, ya_aplicada)."""
    crudo = _leer_crudo()
    T, H = {}, {}
    for n in ESQUEMA:
        df, cab = _a_df(crudo[n])
        T[n], H[n] = _normalizar(df, n, con_fila=True), cab
    for n, esq in ESQUEMA.items():       # ¿ya se guardó esta misma acción? (reintento / doble toque)
        if "ref" in esq and not T[n].empty and (T[n]["ref"] == token).any():
            _leer_todo.clear()
            return None, True
    op = Op(T, H, token)
    builder(op)
    reqs = op.requests(SHEET_IDS)
    if reqs:
        sh.batch_update({"requests": reqs})   # atómica: todo o nada
    _leer_todo.clear()
    return op, False


def reescribir_tabla(nombre, df):
    """Solo para restaurar respaldos / formatear (acciones raras y confirmadas)."""
    ws = sh.worksheet(nombre)
    cols = list(ESQUEMA[nombre])
    df = df.reindex(columns=cols)
    valores = [cols] + [[_py(v) for v in fila] for fila in df.fillna("").values.tolist()]
    previas = ws.row_count
    if len(valores) > previas:
        ws.resize(rows=len(valores) + 50)
    if len(cols) > ws.col_count:
        ws.resize(cols=len(cols))
    ws.update(values=valores, range_name="A1", value_input_option="RAW")
    if previas > len(valores):
        ws.batch_clear([f"A{len(valores) + 1}:Z{previas}"])
    _leer_todo.clear()


# ==========================================
# AYUDAS DE INTERFAZ
# ==========================================
def _version_streamlit():
    try:
        return tuple(int(p) for p in st.__version__.split(".")[:2])
    except Exception:
        return (0, 0)


_ANCHO = {"width": "stretch"} if _version_streamlit() >= (1, 50) else {"use_container_width": True}


def mostrar_df(df, **kw):
    st.dataframe(df, **_ANCHO, **kw)


def aviso(msg, tipo="success"):
    st.session_state["_aviso"] = (tipo, msg)


def mostrar_aviso():
    a = st.session_state.pop("_aviso", None)
    if a:
        tipo, msg = a
        {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}[tipo](msg)


def ver(clave):
    """Contador que sube al guardar; sirve para limpiar los campos del formulario."""
    return st.session_state.get(f"_v_{clave}", 0)


def intentar(clave, builder, grupo=None, limpiar=()):
    """Ejecuta una acción de forma segura. Un doble toque no la repite y, si algo falla,
    dice claramente si se guardó o no. `grupo` = contador que limpia los campos del
    formulario; `limpiar` = claves de sesión que se borran SOLO si se guardó bien."""
    if time.time() - st.session_state.get(f"_t_{clave}", 0) < 4:
        return False  # doble toque: la primera ya se procesó
    token = st.session_state.setdefault(f"_tok_{clave}", uuid.uuid4().hex[:14])
    try:
        op, ya = ejecutar(builder, token)
    except Fallo as e:
        st.error(f"❌ {e}  (No se guardó nada.)")
        return False
    except Exception as e:
        st.error("⚠️ No pude confirmar si se guardó (¿se cortó el internet?). Revisa tu conexión y vuelve a "
                 "tocar el mismo botón: la app comprueba primero si ya quedó guardado y no lo duplica.")
        st.caption(str(e)[:300])
        return False
    st.session_state.pop(f"_tok_{clave}", None)
    st.session_state[f"_t_{clave}"] = time.time()
    g = grupo or clave
    st.session_state[f"_v_{g}"] = ver(g) + 1
    for k in limpiar:
        st.session_state.pop(k, None)
    if ya:
        aviso("ℹ️ Eso ya estaba guardado; no se duplicó.", "info")
    else:
        aviso(op.mensaje or "✅ Listo.")
    st.rerun()


def doble_toque(clave, etiqueta, pregunta="¿Seguro?", si="Sí, confirmar"):
    """Botón de dos toques: el primero pide confirmar, el segundo ejecuta."""
    k = f"_dt_{clave}"
    if st.session_state.get(k):
        st.warning(pregunta)
        c1, c2 = st.columns(2)
        if c1.button(si, key=k + "_si", type="primary", **_ANCHO):
            st.session_state.pop(k, None)
            return True
        if c2.button("Cancelar", key=k + "_no", **_ANCHO):
            st.session_state.pop(k, None)
            st.rerun()
        return False
    if st.button(etiqueta, key=k + "_ini", **_ANCHO):
        st.session_state[k] = True
        st.rerun()
    return False


def filtrar(df, col, periodo):
    if df.empty or periodo == "Todo":
        return df
    f = pd.to_datetime(df[col], errors="coerce")
    hoy = pd.Timestamp(ahora().date())
    if periodo == "Hoy":
        m = f.dt.normalize() == hoy
    elif periodo == "7 días":
        m = f.dt.normalize() >= hoy - pd.Timedelta(days=6)
    else:  # Este mes
        m = (f.dt.year == hoy.year) & (f.dt.month == hoy.month)
    return df[m.fillna(False)]


def opciones_config(D):
    cfg = D["config"]
    cats = cfg[cfg["clave"] == "cat_insumo"]["valor"].astype(str).tolist()
    unis = cfg[cfg["clave"] == "unidad_insumo"]["valor"].astype(str).tolist()
    return (opciones(CATEGORIAS_BASE, cats + D["insumos"]["categoria"].tolist()),
            opciones(UNIDADES_BASE, unis + D["insumos"]["unidad"].tolist()))


def elegir_o_crear(etiqueta, lista, actual, clave, nuevo_label):
    """Selector + campo opcional para crear una opción nueva. Devuelve (valor, es_nueva)."""
    idx = lista.index(actual) if actual in lista else 0
    sel = st.selectbox(etiqueta, lista, index=idx, key=clave)
    nuevo = st.text_input(nuevo_label, key=clave + "_nuevo", placeholder="Déjalo vacío para usar la lista").strip()
    if nuevo:
        return nuevo, nuevo not in lista
    return sel, False


def costo_receta(rid, ri, ins):
    total, faltan = 0.0, []
    for _, i in ri[ri["receta_id"] == rid].iterrows():
        d = ins[ins["nombre"] == i["insumo_nombre"]]
        if d.empty:
            faltan.append(i["insumo_nombre"])
        else:
            total += float(d.iloc[0]["costo_unidad"]) * float(i["cantidad"])
    return total, faltan


def editor_ingredientes(clave, D, inicial=None):
    """Lista de ingredientes pensada para el dedo: un campo numérico por ingrediente."""
    ins = D["insumos"]
    unidad = dict(zip(ins["nombre"], ins["unidad"]))
    costo = dict(zip(ins["nombre"], ins["costo_unidad"]))
    if clave not in st.session_state:
        st.session_state[clave] = [{"rid": uuid.uuid4().hex[:6], "insumo": a, "cantidad": float(b)}
                                   for a, b in (inicial or [])]
    filas = st.session_state[clave]
    total = 0.0
    for fila in list(filas):
        u = unidad.get(fila["insumo"], "?")
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        fila["cantidad"] = c1.number_input(f"{fila['insumo']} ({u})", min_value=0.0,
                                           value=float(fila["cantidad"]), step=paso_para(u), format="%g",
                                           key=f"{clave}_q_{fila['rid']}")
        if c2.button("✕", key=f"{clave}_x_{fila['rid']}", help="Quitar este ingrediente"):
            filas.remove(fila)
            st.rerun()
        total += float(costo.get(fila["insumo"], 0.0)) * fila["cantidad"]
    usados = {f["insumo"] for f in filas}
    libres = [n for n in unidad if n not in usados]
    if libres:
        va = st.session_state.get(clave + "_nadd", 0)
        a1, a2 = st.columns([3, 2])
        nuevo = a1.selectbox("Agregar ingrediente", libres, key=f"{clave}_sel_{va}")
        qn = a2.number_input("Cantidad", min_value=0.0, value=0.0, step=paso_para(unidad[nuevo]),
                             format="%g", key=f"{clave}_cnt_{va}")
        if st.button("➕ Agregar a la receta", key=f"{clave}_add_{va}", **_ANCHO):
            if qn > 0:
                filas.append({"rid": uuid.uuid4().hex[:6], "insumo": nuevo, "cantidad": float(qn)})
                st.session_state[clave + "_nadd"] = va + 1
                st.rerun()
            else:
                st.warning("Indica una cantidad mayor a 0.")
    elif not unidad:
        st.info("Primero registra insumos en 📦 Insumos.")
    return [(f["insumo"], f["cantidad"]) for f in filas if f["cantidad"] > 0], total


# ==========================================
# CABECERA Y MENÚ
# ==========================================
D = cargar_todo()

SECCIONES = ["💰 Vender", "🍳 Producir", "📦 Insumos", "📖 Recetas", "⚙️ Más"]

h1, h2 = st.columns([6, 1], vertical_alignment="center")
h1.markdown("### 💎 Lady Pays")
if h2.button("🔄", help="Actualizar desde Google Sheets (úsalo si editaste la hoja a mano)"):
    _leer_todo.clear()
    st.rerun()

mostrar_aviso()

_hoy = ahora().strftime("%Y-%m-%d")
_fin, _ins, _tan = D["finanzas"], D["insumos"], D["tandas"]
_ventas_hoy = _fin[(_fin["tipo"] == "Ingreso") & (_fin["fecha"].str.startswith(_hoy))]["monto"].sum()
_crit = int((_ins["stock"] <= _ins["stock_minimo"]).sum()) if not _ins.empty else 0
_resumen = f"Hoy **{dinero(_ventas_hoy)}** · Listos **{cant(_tan['stock_disponible'].sum())}** pzas"
if _crit:
    _resumen += f" · ⚠️ **{_crit}** insumos por agotarse"
st.caption(_resumen)

st.session_state.setdefault("nav", SECCIONES[0])
if hasattr(st, "segmented_control"):
    _sel = st.segmented_control("Menú", SECCIONES, key="nav", label_visibility="collapsed")
else:
    _sel = st.radio("Menú", SECCIONES, horizontal=True, key="nav", label_visibility="collapsed")
if _sel:
    st.session_state["_nav_prev"] = _sel
_sel = _sel or st.session_state.get("_nav_prev", SECCIONES[0])


# ==========================================
# 💰 VENDER
# ==========================================
def pagina_vender(D):
    rec, tan, ven = D["recetas"], D["tandas"], D["ventas"]
    hay = tan[tan["stock_disponible"] >= 1 - 1e-9]
    if hay.empty:
        st.info("No hay pays listos para vender. Registra una producción en **🍳 Producir**.")
    else:
        v = ver("vender")
        total_p = hay.groupby("receta_nombre")["stock_disponible"].sum()
        productos = total_p.index.tolist()
        prod = st.selectbox("¿Qué vendes?", productos, key="v_prod",
                            format_func=lambda p: f"{p} · {cant(total_p[p])} disp.")
        max_c = int(math.floor(total_p[prod] + 1e-9))
        r = rec[rec["nombre"] == prod]
        precio0 = float(r.iloc[0]["precio_venta"]) if not r.empty else 0.0
        if r.empty:
            st.caption("La receta ya no existe: escribe el precio a mano.")
        c1, c2 = st.columns(2)
        n = c1.number_input("Cantidad", min_value=1, max_value=max_c, value=1, step=1, key=f"v_cant_{prod}_{v}")
        precio = c2.number_input("Precio c/u ($)", min_value=0.0, value=precio0, step=5.0, format="%.2f",
                                 key=f"v_precio_{prod}_{v}", help="Cámbialo si haces descuento.")
        nota = st.text_input("Nota (opcional)", key=f"v_nota_{v}", placeholder="Ej. Cliente: María")
        st.markdown(f"#### Total: {dinero(n * precio)}")
        if st.button("💰 Cobrar", type="primary", key="btn_cobrar", **_ANCHO):
            intentar("vender", b_vender(prod, int(n), float(precio), nota.strip()))

        # Aviso de caducidad
        urgentes = []
        for l in hay.itertuples():
            d = dias_para_caducar(l.caduca) if l.caduca else None
            if d is not None and d <= 2:
                cuando = "ya caducó" if d < 0 else ("caduca hoy" if d == 0 else f"caduca en {d} día(s)")
                urgentes.append(f"⏰ Lote #{l.id} · {l.receta_nombre} ({cant(l.stock_disponible)} pzas): {cuando}")
        if urgentes:
            st.warning("\n\n".join(urgentes[:5]))

    if not ven.empty:
        with st.expander("🧾 Ventas recientes (aquí puedes anular)"):
            for r in ven.sort_values("id", ascending=False).head(6).itertuples():
                anulada = r.estado == "Anulada"
                txt = f"**#{r.id}** · {r.fecha[5:16]} · {cant(r.cantidad)} × {r.producto} · {dinero(r.total)}"
                st.markdown(f"~~{txt}~~ ❌ anulada" if anulada else txt)
                if not anulada and doble_toque(f"anular_{r.id}", f"↩️ Anular venta #{r.id}",
                                               "¿Anular? Las piezas vuelven al lote y se quita el ingreso.",
                                               "Sí, anular"):
                    intentar(f"anular_{r.id}", b_anular_venta(int(r.id)))


# ==========================================
# 🍳 PRODUCIR
# ==========================================
def pagina_producir(D):
    rec, ins, ri, tan = D["recetas"], D["insumos"], D["receta_ingredientes"], D["tandas"]
    if rec.empty:
        st.info("Aún no hay recetas. Crea la primera en **📖 Recetas**.")
    else:
        v = ver("producir")
        receta = st.selectbox("Receta", rec["nombre"].tolist(), key="p_receta")
        c1, c2 = st.columns([1, 2])
        n = c1.number_input("Tandas", min_value=1, step=1, value=1, key=f"p_n_{v}")
        notas = c2.text_input("Notas (opcional)", key=f"p_notas_{v}", placeholder="Ej. horneado en la mañana")
        r = rec[rec["nombre"] == receta].iloc[0]
        rinde = rinde_de(r["rinde"])
        piezas = int(n) * rinde
        ing = ri[ri["receta_id"] == int(r["id"])]
        filas, ok, costo, faltan = [], not ing.empty, 0.0, []
        for _, i in ing.iterrows():
            nec = r4(float(i["cantidad"]) * n)
            d = ins[ins["nombre"] == i["insumo_nombre"]]
            if d.empty:
                filas.append({"Ingrediente": i["insumo_nombre"], "Necesitas": cant(nec), "Tienes": "—", "": "❌"})
                faltan.append(i["insumo_nombre"])
                ok = False
                continue
            tiene, u = float(d.iloc[0]["stock"]), d.iloc[0]["unidad"]
            costo += nec * float(d.iloc[0]["costo_unidad"])
            alcanza = tiene + 1e-9 >= nec
            if not alcanza:
                faltan.append(i["insumo_nombre"])
                ok = False
            filas.append({"Ingrediente": i["insumo_nombre"], "Necesitas": f"{cant(nec)} {u}",
                          "Tienes": f"{cant(tiene)} {u}", "": "✅" if alcanza else "❌"})
        if ing.empty:
            st.warning("Esta receta no tiene ingredientes. Agrégalos en **📖 Recetas → Editar**.")
        else:
            mostrar_df(pd.DataFrame(filas), hide_index=True)
            if ok:
                st.info(f"Obtendrás **{piezas} piezas** ({rinde} por tanda)\n\n"
                        f"💰 Costo total **{dinero(costo)}** · **{dinero(costo / piezas)} por pieza**")
                if rinde == 1:
                    st.caption("Esta receta rinde 1 pieza por tanda. Si rinde más, cámbialo en 📖 Recetas → Editar.")
            else:
                st.error("Te falta: " + ", ".join(faltan))
        if st.button("🍳 Producir", type="primary", disabled=not ok, key="btn_producir", **_ANCHO):
            intentar("producir", b_producir(receta, int(n), notas.strip()))

    st.markdown("#### Lotes con existencia")
    act = tan[tan["stock_disponible"] > 0].sort_values("id", ascending=False)
    if act.empty:
        st.caption("No hay lotes con piezas disponibles.")
    else:
        vista = pd.DataFrame({"Lote": act["id"], "Producto": act["receta_nombre"],
                              "Disp.": act["stock_disponible"].map(cant),
                              "$/pieza": [dinero(c / p) if p > 0 else "—" for c, p in
                                          zip(act["costo_total"], act["cantidad_producida"])],
                              "Caduca": act["caduca"].str[:10]})
        mostrar_df(vista, hide_index=True)

    if not tan.empty:
        with st.expander("✏️ Corregir o borrar un lote"):
            etiq = {int(r.id): f"#{r.id} · {r.receta_nombre} · disp {cant(r.stock_disponible)}"
                    for r in tan.sort_values("id", ascending=False).itertuples()}
            tid = st.selectbox("Lote", list(etiq), format_func=lambda x: etiq[x], key="lote_sel")
            fl = tan[tan["id"] == tid].iloc[0]
            vv = ver(f"lote_{tid}")
            prod_l = st.number_input("Piezas producidas en total", min_value=1.0,
                                     value=max(float(fl["cantidad_producida"]), 1.0), step=1.0,
                                     key=f"lt_p_{tid}_{vv}",
                                     help="Corrígelo si este lote se registró antes de indicar cuánto rinde la receta.")
            disp = st.number_input("Piezas disponibles", min_value=0.0, value=float(fl["stock_disponible"]),
                                   step=1.0, key=f"lt_d_{tid}_{vv}")
            notas_l = st.text_input("Notas", value=str(fl["notas"]), key=f"lt_n_{tid}_{vv}")
            costo_l = st.number_input("Costo total del lote ($)", min_value=0.0, value=float(fl["costo_total"]),
                                      format="%.2f", key=f"lt_c_{tid}_{vv}")
            if st.button("💾 Guardar cambios", type="primary", key=f"lt_g_{tid}", **_ANCHO):
                intentar(f"lote_{tid}", b_corregir_lote(int(tid), prod_l, disp, notas_l.strip(), costo_l))
            devolver = st.checkbox("Al borrar, devolver los insumos al inventario (solo si se registró por error)",
                                   key=f"lt_dev_{tid}")
            if doble_toque(f"borrar_lote_{tid}", "🗑️ Borrar lote", "¿Borrar este lote?", "Sí, borrar"):
                intentar(f"borrar_lote_{tid}", b_borrar_lote(int(tid), devolver))


# ==========================================
# 📦 INSUMOS
# ==========================================
def pagina_insumos(D):
    ins = D["insumos"]
    cats_op, unis_op = opciones_config(D)

    # --- lista ---
    f1, f2 = st.columns([3, 2])
    busq = f1.text_input("🔍 Buscar", key="busq_ins", placeholder="Ej. leche")
    solo = f2.toggle("Solo críticos", key="solo_crit")
    if ins.empty:
        st.info("Aún no hay insumos. Abre **🆕 Nuevo insumo** para registrar el primero.")
    else:
        vista = ins.copy()
        vista["crit"] = vista["stock"] <= vista["stock_minimo"]
        if busq:
            vista = vista[vista["nombre"].str.contains(busq, case=False, na=False)]
        if solo:
            vista = vista[vista["crit"]]
        tabla = pd.DataFrame({
            "Insumo": [("🔴 " if c else "") + n for n, c in zip(vista["nombre"], vista["crit"])],
            "Stock": [f"{cant(s)} {u}" for s, u in zip(vista["stock"], vista["unidad"])],
            "Mín.": vista["stock_minimo"].map(cant),
            "$/u": vista["costo_unidad"].map(dinero)})
        mostrar_df(tabla, hide_index=True)
        valor = float((ins["stock"] * ins["costo_unidad"]).sum())
        st.caption(f"{len(ins)} insumos · valor del inventario {dinero(valor)}")

    v = ver("insumo")
    # --- comprar ---
    with st.expander("🔁 Comprar / reabastecer"):
        if ins.empty:
            st.info("Primero registra un insumo.")
        else:
            nombre = st.selectbox("Insumo", ins["nombre"].tolist(), key="c_ins")
            f = ins[ins["nombre"] == nombre].iloc[0]
            u = f["unidad"]
            st.caption(f"Tienes **{cant(f['stock'])} {u}** · costo actual **{dinero(f['costo_unidad'])}** por {u}")
            conv = conversiones(u)
            c1, c2 = st.columns(2)
            q = c1.number_input("Cantidad comprada", min_value=0.0, value=0.0, step=paso_para(u),
                                format="%g", key=f"c_q_{nombre}_{v}")
            if len(conv) > 1:
                et = c2.selectbox("Unidad de compra", [e for e, _ in conv], key=f"c_u_{nombre}_{v}")
                factor = dict(conv)[et]
            else:
                c2.write("")
                factor = 1.0
            total = st.number_input("Total pagado ($)", min_value=0.0, value=0.0, step=10.0, format="%.2f",
                                    key=f"c_t_{nombre}_{v}")
            minimo = st.number_input("Stock mínimo", min_value=0.0, value=float(f["stock_minimo"]),
                                     step=paso_para(u), format="%g", key=f"c_m_{nombre}_{v}")
            en_inv = r4(q * factor)
            if en_inv > 0 and total > 0:
                st.caption(f"Sumas {cant(en_inv)} {u} · nuevo costo {dinero(total / en_inv)} por {u}")
            if st.button("Registrar compra", type="primary", key="btn_compra", **_ANCHO):
                if en_inv <= 0:
                    st.error("Indica la cantidad comprada.")
                elif total <= 0:
                    st.error("Escribe cuánto pagaste en total. (Si fue regalo, ajusta el stock en ✏️ Editar.)")
                else:
                    intentar("comprar", b_comprar(nombre, en_inv, total, minimo), grupo="insumo")

    # --- nuevo ---
    with st.expander("🆕 Nuevo insumo"):
        with st.form("form_nuevo_insumo"):
            nombre = st.text_input("Nombre", key=f"n_nom_{v}").strip()
            cat, cat_nueva = elegir_o_crear("Categoría", cats_op, cats_op[0], f"n_cat_{v}", "¿Categoría nueva? (opcional)")
            uni, uni_nueva = elegir_o_crear("Unidad", unis_op, unis_op[0], f"n_uni_{v}", "¿Unidad nueva? (opcional)")
            costo = st.number_input("Costo por unidad ($)", min_value=0.0, format="%.2f", key=f"n_cos_{v}")
            stock = st.number_input("Cantidad que tienes ahora", min_value=0.0, key=f"n_stk_{v}", format="%g")
            minimo = st.number_input("Stock mínimo", min_value=0.0, key=f"n_min_{v}", format="%g")
            gasto = st.checkbox("Registrar esta compra como egreso", value=True, key=f"n_gas_{v}",
                                help="Desmárcalo si solo cargas tu inventario inicial.")
            if st.form_submit_button("Guardar insumo", type="primary", **_ANCHO):
                if not nombre:
                    st.error("Escribe el nombre del insumo.")
                else:
                    intentar("nuevo_insumo", b_nuevo_insumo(nombre, cat, uni, costo, stock, minimo, gasto,
                                                            cat_nueva, uni_nueva), grupo="insumo")

    # --- editar / eliminar ---
    with st.expander("✏️ Editar o eliminar"):
        if ins.empty:
            st.info("No hay insumos.")
        else:
            sel = st.selectbox("Insumo", ins["nombre"].tolist(), key="e_ins")
            f = ins[ins["nombre"] == sel].iloc[0]
            k = f"{sel}_{v}"
            e_nom = st.text_input("Nombre", value=str(f["nombre"]), key=f"e_nom_{k}").strip()
            e_cat, e_cat_n = elegir_o_crear("Categoría", cats_op, f["categoria"], f"e_cat_{k}", "¿Categoría nueva? (opcional)")
            e_uni, e_uni_n = elegir_o_crear("Unidad", unis_op, f["unidad"], f"e_uni_{k}", "¿Unidad nueva? (opcional)")
            e_cos = st.number_input("Costo por unidad ($)", min_value=0.0, value=float(f["costo_unidad"]),
                                    format="%.2f", key=f"e_cos_{k}")
            e_stk = st.number_input("Stock actual exacto", min_value=0.0, value=float(f["stock"]),
                                    format="%g", key=f"e_stk_{k}")
            e_min = st.number_input("Stock mínimo", min_value=0.0, value=float(f["stock_minimo"]),
                                    format="%g", key=f"e_min_{k}")
            if st.button("💾 Guardar cambios", type="primary", key="btn_edit_ins", **_ANCHO):
                if not e_nom:
                    st.error("El nombre no puede estar vacío.")
                else:
                    intentar("editar_insumo", b_editar_insumo(sel, e_nom, e_cat, e_uni, e_cos, e_stk, e_min,
                                                              e_cat_n, e_uni_n), grupo="insumo")
            if doble_toque(f"borrar_ins_{sel}", "🗑️ Eliminar insumo", f"¿Eliminar «{sel}»?", "Sí, eliminar"):
                intentar(f"borrar_ins_{sel}", b_borrar_insumo(sel), grupo="insumo")


# ==========================================
# 📖 RECETAS
# ==========================================
def pagina_recetas(D):
    rec, ri, ins = D["recetas"], D["receta_ingredientes"], D["insumos"]
    margen = st.slider("Margen que quieres ganar (%)", 10, 80, 50, 5, key="margen",
                       help="Se usa para sugerirte el precio de venta de cada receta.")

    if rec.empty:
        st.info("Aún no hay recetas. Crea la primera abajo.")
    for r in rec.itertuples():
        costo, faltan = costo_receta(r.id, ri, ins)
        rinde = rinde_de(r.rinde)
        costo_pz = costo / rinde
        with st.expander(f"🍰 {r.nombre} · {dinero(r.precio_venta)} c/u"):
            filas = []
            for _, i in ri[ri["receta_id"] == r.id].iterrows():
                d = ins[ins["nombre"] == i["insumo_nombre"]]
                u = d.iloc[0]["unidad"] if not d.empty else "—"
                c = float(d.iloc[0]["costo_unidad"]) * float(i["cantidad"]) if not d.empty else 0.0
                filas.append({"Ingrediente": i["insumo_nombre"], "Cantidad": f"{cant(i['cantidad'])} {u}",
                              "Costo": dinero(c)})
            if filas:
                mostrar_df(pd.DataFrame(filas), hide_index=True)
            else:
                st.caption("Sin ingredientes todavía.")
            if faltan:
                st.warning("Ya no existen en el inventario: " + ", ".join(faltan))
            gan = r.precio_venta - costo_pz
            pct = (gan / r.precio_venta * 100) if r.precio_venta > 0 else 0
            st.markdown(f"Una tanda cuesta **{dinero(costo)}** y rinde **{rinde}** piezas\n\n"
                        f"Costo por pieza **{dinero(costo_pz)}** · Ganancia por pieza **{dinero(gan)}** ({pct:.0f}%)"
                        + (f"\n\nDura **{cant(r.vida_util_dias)}** días" if r.vida_util_dias > 0 else ""))
            if costo_pz > 0:
                sug = precio_sugerido(costo_pz, margen)
                if abs(sug - r.precio_venta) > 0.5:
                    st.caption(f"💡 Para ganar {margen}% el precio sería **{dinero(sug)}**.")
                    if st.button(f"Usar {dinero(sug)}", key=f"usar_precio_{r.id}"):
                        intentar(f"precio_{r.id}", b_precio_receta(int(r.id), sug))
                else:
                    st.caption(f"✅ Tu precio ya da cerca de {margen}% de margen.")

    vr = ver("receta")
    with st.expander("🆕 Nueva receta"):
        nom = st.text_input("Nombre de la receta", key=f"r_nom_{vr}").strip()
        c1, c2 = st.columns(2)
        cat = c1.text_input("Categoría", value="PAYS", key=f"r_cat_{vr}").strip().upper()
        rinde_n = c2.number_input("Una tanda rinde (piezas)", min_value=1, step=1, value=1, key=f"r_rin_{vr}",
                                  help="Cuántos pays salen de una tanda. Ej. 20")
        c3, c4 = st.columns(2)
        precio = c3.number_input("Precio por pieza ($)", min_value=0.0, format="%.2f", key=f"r_pre_{vr}")
        vida = c4.number_input("Dura (días)", min_value=0, step=1, value=0, key=f"r_vid_{vr}",
                               help="0 = no avisar caducidad")
        ingr, costo_prev = editor_ingredientes(f"ing_nueva_{vr}", D)
        if ingr:
            cpz = costo_prev / int(rinde_n)
            st.info(f"Una tanda cuesta **{dinero(costo_prev)}** → **{dinero(cpz)} por pieza**\n\n"
                    f"Ganancia por pieza **{dinero(precio - cpz)}**"
                    + (f" · Precio sugerido **{dinero(precio_sugerido(cpz, margen))}**" if cpz > 0 else ""))
        if st.button("💾 Guardar receta", type="primary", key="btn_save_rec", **_ANCHO):
            if not nom:
                st.error("Escribe el nombre de la receta.")
            elif not ingr:
                st.error("Agrega al menos un ingrediente.")
            else:
                intentar("receta", b_crear_receta(nom, cat, precio, int(vida), int(rinde_n), ingr))

    if not rec.empty:
        with st.expander("✏️ Editar o eliminar receta"):
            etiq = {int(r.id): r.nombre for r in rec.itertuples()}
            rid = st.selectbox("Receta", list(etiq), format_func=lambda x: etiq[x], key="edit_rec_sel")
            fr = rec[rec["id"] == rid].iloc[0]
            k = f"{rid}_{ver(f'editrec_{rid}')}"
            e_nom = st.text_input("Nombre", value=str(fr["nombre"]), key=f"er_nom_{k}").strip()
            c1, c2 = st.columns(2)
            e_cat = c1.text_input("Categoría", value=str(fr["categoria"]), key=f"er_cat_{k}").strip().upper()
            e_rin = c2.number_input("Una tanda rinde (piezas)", min_value=1, step=1, value=rinde_de(fr["rinde"]),
                                    key=f"er_rin_{k}", help="Aplica a las tandas que produzcas desde ahora.")
            c3, c4 = st.columns(2)
            e_pre = c3.number_input("Precio por pieza ($)", min_value=0.0, value=float(fr["precio_venta"]),
                                    format="%.2f", key=f"er_pre_{k}")
            e_vid = c4.number_input("Dura (días)", min_value=0, step=1, value=int(fr["vida_util_dias"]),
                                    key=f"er_vid_{k}")
            if st.button("💾 Guardar datos", type="primary", key=f"er_g_{rid}", **_ANCHO):
                if not e_nom:
                    st.error("El nombre no puede estar vacío.")
                else:
                    intentar(f"editrec_{rid}", b_editar_receta(int(rid), e_nom, e_cat, e_pre, int(e_vid), int(e_rin)))
            st.markdown("**Ingredientes**")
            actuales = [(i["insumo_nombre"], float(i["cantidad"]))
                        for _, i in ri[ri["receta_id"] == rid].iterrows()]
            nuevos, _ = editor_ingredientes(f"ing_edit_{rid}", D, actuales)
            if st.button("💾 Guardar ingredientes", type="primary", key=f"er_i_{rid}", **_ANCHO):
                if not nuevos:
                    st.error("La receta debe tener al menos un ingrediente.")
                else:
                    intentar(f"ingrec_{rid}", b_ingredientes_receta(int(rid), nuevos),
                             limpiar=(f"ing_edit_{rid}",))
            if doble_toque(f"borrar_rec_{rid}", "🗑️ Eliminar receta", f"¿Eliminar «{fr['nombre']}»?", "Sí, eliminar"):
                intentar(f"borrar_rec_{rid}", b_borrar_receta(int(rid)))


# ==========================================
# ⚙️ MÁS: FINANZAS, COMPRAS, MERMAS, RESPALDOS
# ==========================================
def pagina_finanzas(D):
    fin, ven, mer = D["finanzas"], D["ventas"], D["mermas"]
    periodo = st.radio("Periodo", ["Hoy", "7 días", "Este mes", "Todo"], index=2, horizontal=True, key="fin_per")

    vp = filtrar(ven[ven["estado"] != "Anulada"], "fecha", periodo)
    mp = filtrar(mer, "fecha", periodo)
    fp = filtrar(fin, "fecha", periodo)
    egr = fp[fp["tipo"] == "Egreso"].copy()
    es_compra = egr["categoria"].str.startswith("Insumos") | ((egr["categoria"] == "") & egr["descripcion"].str.startswith("Compra de"))
    otros_gastos = float(egr[~es_compra]["monto"].sum())
    ing_v, costo_v, perd = float(vp["total"].sum()), float(vp["costo"].sum()), float(mp["costo_estimado"].sum())
    ganancia = ing_v - costo_v - perd - otros_gastos

    st.metric("Ganancia real", dinero(ganancia),
              help="Ventas − costo de lo vendido − mermas − otros gastos. Lo que compraste y sigue en tu alacena no cuenta como gasto.")
    st.caption(f"Ventas {dinero(ing_v)} − costo de lo vendido {dinero(costo_v)} − mermas {dinero(perd)} "
               f"− otros gastos {dinero(otros_gastos)}")
    ing_c = float(fp[fp["tipo"] == "Ingreso"]["monto"].sum())
    egr_c = float(egr["monto"].sum())
    st.caption(f"💵 Caja del periodo: entró {dinero(ing_c)} · salió {dinero(egr_c)} · balance {dinero(ing_c - egr_c)}")
    if periodo != "Hoy" and ing_c > ing_v + 0.01:
        st.caption("Las ventas anteriores a esta versión no guardaron producto ni costo: cuentan en la caja, no en la ganancia real.")

    if not vp.empty:
        st.markdown("#### Por producto")
        g = vp.groupby("producto").agg(Piezas=("cantidad", "sum"), Ventas=("total", "sum"),
                                       Costo=("costo", "sum")).reset_index()
        g["Ganancia"] = g["Ventas"] - g["Costo"]
        g = g.sort_values("Piezas", ascending=False)
        mostrar_df(pd.DataFrame({"Producto": g["producto"], "Pzas": g["Piezas"].map(cant),
                                 "Ventas": g["Ventas"].map(dinero), "Ganancia": g["Ganancia"].map(dinero)}),
                   hide_index=True)
    if not egr.empty:
        st.markdown("#### Gastos por categoría")
        egr["Categoría"] = [c if c else ("Insumos: sin categoría" if str(d).startswith("Compra de") else "Sin categoría")
                            for c, d in zip(egr["categoria"], egr["descripcion"])]
        g = egr.groupby("Categoría")["monto"].sum().sort_values(ascending=False).reset_index()
        mostrar_df(pd.DataFrame({"Categoría": g["Categoría"], "Gastado": g["monto"].map(dinero)}), hide_index=True)

    with st.expander("➕ Registrar ingreso / gasto manual"):
        vm = ver("mov")
        tipo = st.radio("Tipo", ["Egreso", "Ingreso"], horizontal=True, key=f"mv_t_{vm}")
        monto = st.number_input("Monto ($)", min_value=0.0, format="%.2f", key=f"mv_m_{vm}")
        cat = st.selectbox("Categoría", CATEGORIAS_GASTO if tipo == "Egreso" else ["Otros ingresos"], key=f"mv_c_{vm}_{tipo}")
        desc = st.text_input("Descripción", key=f"mv_d_{vm}")
        if st.button("Guardar movimiento", type="primary", key="btn_mov", **_ANCHO):
            if monto > 0 and desc.strip():
                intentar("mov", b_movimiento(tipo, monto, cat, desc.strip()))
            else:
                st.error("Indica un monto mayor a 0 y una descripción.")

    with st.expander("📜 Movimientos y correcciones"):
        if fp.empty:
            st.caption("No hay movimientos en este periodo.")
        else:
            vista = fp.sort_values("id", ascending=False).head(30)
            mostrar_df(pd.DataFrame({"#": vista["id"], "Fecha": vista["fecha"].str[:16], "Tipo": vista["tipo"],
                                     "Monto": vista["monto"].map(dinero), "Descripción": vista["descripcion"]}),
                       hide_index=True)
            st.download_button("📥 Descargar (CSV)", data=fp.to_csv(index=False).encode("utf-8-sig"),
                               file_name="finanzas_ladypays.csv", mime="text/csv", key="dl_fin")
            etiq = {int(r.id): f"#{r.id} · {r.tipo} {dinero(r.monto)} · {r.descripcion[:30]}"
                    for r in fin.sort_values("id", ascending=False).itertuples()}
            fid = st.selectbox("Corregir movimiento", list(etiq), format_func=lambda x: etiq[x], key="fin_edit_sel")
            fm = fin[fin["id"] == fid].iloc[0]
            if fm["ref"] and (ven["ref"] == fm["ref"]).any():
                st.info("Es una venta. Para corregirla, anúlala en **💰 Vender → Ventas recientes**.")
            else:
                k = f"{fid}_{ver(f'editmov_{fid}')}"
                e_tipo = st.radio("Tipo", ["Ingreso", "Egreso"], index=0 if fm["tipo"] == "Ingreso" else 1,
                                  horizontal=True, key=f"ef_t_{k}")
                e_monto = st.number_input("Monto ($)", min_value=0.0, value=float(fm["monto"]), format="%.2f", key=f"ef_m_{k}")
                e_cat = st.text_input("Categoría", value=str(fm["categoria"]), key=f"ef_c_{k}").strip()
                e_desc = st.text_input("Descripción", value=str(fm["descripcion"]), key=f"ef_d_{k}").strip()
                if st.button("💾 Guardar", type="primary", key=f"ef_g_{fid}", **_ANCHO):
                    intentar(f"editmov_{fid}", b_editar_movimiento(int(fid), e_tipo, e_monto, e_cat, e_desc))
                if doble_toque(f"borrar_mov_{fid}", "🗑️ Eliminar movimiento", "¿Eliminar este movimiento?", "Sí, eliminar"):
                    intentar(f"borrar_mov_{fid}", b_borrar_movimiento(int(fid)))


def pagina_compras(D):
    ins, rec, ri = D["insumos"], D["recetas"], D["receta_ingredientes"]
    if ins.empty:
        st.info("Aún no hay insumos.")
        return
    plan = {}
    with st.expander("🗓️ Plan de producción (opcional)"):
        st.caption("¿Cuántas tandas harás pronto? La lista incluirá lo que haga falta para hacerlas.")
        for r in rec.itertuples():
            n = st.number_input(r.nombre, min_value=0, step=1, value=0, key=f"plan_{r.id}")
            if n:
                plan[int(r.id)] = int(n)
    necesidad = {}
    for rid, n in plan.items():
        for _, i in ri[ri["receta_id"] == rid].iterrows():
            necesidad[i["insumo_nombre"]] = necesidad.get(i["insumo_nombre"], 0.0) + float(i["cantidad"]) * n

    filas = []
    for r in ins.itertuples():
        need = necesidad.get(r.nombre, 0.0)
        if r.stock <= r.stock_minimo + 1e-9 or r.stock + 1e-9 < need:
            objetivo = max(2 * r.stock_minimo, need + r.stock_minimo)
            comprar = redondeo_compra(max(objetivo - r.stock, 1.0), r.unidad)
            filas.append({"nombre": r.nombre, "stock": r.stock, "unidad": r.unidad, "comprar": comprar,
                          "costo": comprar * r.costo_unidad})
    if not filas:
        st.success("✔️ Todo en orden: ningún insumo está en el mínimo.")
        return
    total = sum(f["costo"] for f in filas)
    st.metric("Inversión estimada", dinero(total))
    mostrar_df(pd.DataFrame({"Insumo": [f["nombre"] for f in filas],
                             "Tienes": [f"{cant(f['stock'])} {f['unidad']}" for f in filas],
                             "Comprar": [f"{cant(f['comprar'])} {f['unidad']}" for f in filas],
                             "Costo": [dinero(f["costo"]) for f in filas]}), hide_index=True)
    st.caption("Se sugiere comprar hasta el doble de tu mínimo (o lo que pida tu plan), para no quedar otra vez en crítico.")
    txt = f"LISTA DE COMPRAS - LADY PAYS ({ahora().strftime('%d/%m/%Y')})\n"
    txt += "".join(f"- {f['nombre']}: {cant(f['comprar'])} {f['unidad']}\n" for f in filas)
    txt += f"Total estimado: {dinero(total)}"
    st.link_button("📲 Enviar por WhatsApp", f"https://wa.me/?text={quote(txt)}", **_ANCHO)
    st.download_button("📥 Descargar (.txt)", data=txt, file_name="lista_compras.txt", key="dl_compras", **_ANCHO)


def pagina_mermas(D):
    ins, tan, mer = D["insumos"], D["tandas"], D["mermas"]
    v = ver("merma")
    tipo = st.radio("¿Qué se perdió?", ["Insumo", "Pay terminado"], horizontal=True, key="m_tipo")
    if tipo == "Insumo":
        con = ins[ins["stock"] > 0]
        if con.empty:
            st.info("No hay insumos con stock.")
        else:
            nom = st.selectbox("Insumo", con["nombre"].tolist(), key="m_ins")
            f = con[con["nombre"] == nom].iloc[0]
            st.caption(f"Disponible: {cant(f['stock'])} {f['unidad']}")
            c = st.number_input(f"Cantidad perdida ({f['unidad']})", min_value=0.0, max_value=float(f["stock"]),
                                value=0.0, step=paso_para(f["unidad"]), format="%g", key=f"m_c_{nom}_{v}")
            motivo = st.selectbox("Motivo", MOTIVOS_MERMA, key=f"m_mot_{v}")
            detalle = st.text_input("Detalle (opcional)", key=f"m_det_{v}")
            if st.button("Registrar merma", type="primary", key="btn_merma_i", **_ANCHO):
                if c <= 0:
                    st.error("Indica una cantidad mayor a 0.")
                else:
                    intentar("merma_ins", b_merma_insumo(nom, c, motivo, detalle.strip()), grupo="merma")
    else:
        hay = tan[tan["stock_disponible"] >= 1 - 1e-9]
        if hay.empty:
            st.info("No hay pays disponibles.")
        else:
            tot = hay.groupby("receta_nombre")["stock_disponible"].sum()
            prod = st.selectbox("Producto", tot.index.tolist(), key="m_prod",
                                format_func=lambda p: f"{p} · {cant(tot[p])} disp.")
            c = st.number_input("Piezas perdidas", min_value=1, max_value=int(math.floor(tot[prod] + 1e-9)),
                                value=1, step=1, key=f"m_cp_{prod}_{v}")
            motivo = st.selectbox("Motivo", MOTIVOS_MERMA, key=f"m_motp_{v}")
            detalle = st.text_input("Detalle (opcional)", key=f"m_detp_{v}")
            if st.button("Registrar merma", type="primary", key="btn_merma_p", **_ANCHO):
                intentar("merma_prod", b_merma_producto(prod, int(c), motivo, detalle.strip()), grupo="merma")

    st.markdown("#### ¿Por qué pierdes?")
    if mer.empty:
        st.caption("Aún no hay mermas registradas. 🎉")
        return
    g = mer.copy()
    g["m"] = g["motivo"].where(g["motivo"].isin(MOTIVOS_MERMA), "Otro")
    res = g.groupby("m").agg(Veces=("id", "count"), Perdido=("costo_estimado", "sum")).sort_values("Perdido", ascending=False).reset_index()
    mostrar_df(pd.DataFrame({"Motivo": res["m"], "Veces": res["Veces"], "Perdido": res["Perdido"].map(dinero)}), hide_index=True)
    st.caption(f"Pérdida total estimada: {dinero(mer['costo_estimado'].sum())}")

    with st.expander("📜 Historial y correcciones"):
        rec_m = mer.sort_values("id", ascending=False).head(15)
        mostrar_df(pd.DataFrame({"#": rec_m["id"], "Fecha": rec_m["fecha"].str[:10], "Qué": rec_m["nombre"],
                                 "Cant.": rec_m["cantidad"].map(cant), "Motivo": rec_m["motivo"]}), hide_index=True)
        etiq = {int(r.id): f"#{r.id} · {r.nombre} ({cant(r.cantidad)} {r.unidad})"
                for r in mer.sort_values("id", ascending=False).itertuples()}
        mid = st.selectbox("Merma a corregir", list(etiq), format_func=lambda x: etiq[x], key="merma_edit_sel")
        fm = mer[mer["id"] == mid].iloc[0]
        k = f"{mid}_{ver(f'editmerma_{mid}')}"
        e_c = st.number_input("Cantidad", min_value=0.0, value=float(fm["cantidad"]), format="%g", key=f"em_c_{k}")
        e_cost = st.number_input("Costo estimado ($)", min_value=0.0, value=float(fm["costo_estimado"]),
                                 format="%.2f", key=f"em_k_{k}")
        mots = opciones(MOTIVOS_MERMA, [fm["motivo"]])
        e_mot = st.selectbox("Motivo", mots, index=mots.index(fm["motivo"]) if fm["motivo"] in mots else 0, key=f"em_m_{k}")
        e_det = st.text_input("Detalle", value=str(fm["detalle"]), key=f"em_d_{k}")
        st.caption("Corregir una merma no reajusta el inventario.")
        if st.button("💾 Guardar", type="primary", key=f"em_g_{mid}", **_ANCHO):
            intentar(f"editmerma_{mid}", b_editar_merma(int(mid), e_c, e_cost, e_mot, e_det.strip()))
        devolver = st.checkbox("Al eliminar, devolver la cantidad al inventario (solo insumos)", key=f"em_dev_{mid}") \
            if fm["tipo"] == "Insumo" else False
        if doble_toque(f"borrar_merma_{mid}", "🗑️ Eliminar merma", "¿Eliminar esta merma?", "Sí, eliminar"):
            intentar(f"borrar_merma_{mid}", b_borrar_merma(int(mid), devolver))


def pagina_respaldos(D):
    st.markdown("#### 💾 Copia de seguridad")
    st.download_button("⬇️ Descargar respaldo (JSON)",
                       data=json.dumps({n: D[n].to_dict(orient="records") for n in ESQUEMA},
                                       indent=2, ensure_ascii=False, default=str),
                       file_name=f"respaldo_ladypays_{ahora().strftime('%Y%m%d')}.json",
                       mime="application/json", key="dl_respaldo", **_ANCHO)
    st.caption("Descarga uno cada semana.")

    st.markdown("#### ♻️ Restaurar")
    archivo = st.file_uploader("Sube un respaldo (.json)", type="json", key="up_respaldo")
    if archivo is not None:
        try:
            datos = json.load(archivo)
            valido = isinstance(datos, dict) and any(n in datos for n in ESQUEMA)
        except Exception:
            valido = False
        if not valido:
            st.error("El archivo no parece un respaldo válido de Lady Pays.")
        elif doble_toque("restaurar", "♻️ Restaurar respaldo", "Esto REEMPLAZA los datos actuales. ¿Seguro?", "Sí, restaurar"):
            try:
                for n in ESQUEMA:
                    if n in datos:
                        reescribir_tabla(n, _normalizar(pd.DataFrame(datos[n]), n))
                aviso("✅ Respaldo restaurado.")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo restaurar completo: {e}")

    st.markdown("#### ⚠️ Borrar todo")
    st.caption("Descarga un respaldo antes. Esto vacía todas las pestañas.")
    if st.text_input("Escribe BORRAR para habilitar", key="txt_borrar") == "BORRAR":
        if doble_toque("formatear", "Formatear sistema", "Se borrará TODA la información. ¿Seguro?", "Sí, borrar todo"):
            try:
                for n in ESQUEMA:
                    reescribir_tabla(n, pd.DataFrame(columns=list(ESQUEMA[n])))
                aviso("Sistema restablecido.", "warning")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo completar: {e}")


def pagina_mas(D):
    sec = st.selectbox("Sección", ["📈 Finanzas", "🛒 Lista de compras", "🗑️ Mermas", "💾 Respaldos"],
                       key="mas_sec", label_visibility="collapsed")
    {"📈 Finanzas": pagina_finanzas, "🛒 Lista de compras": pagina_compras,
     "🗑️ Mermas": pagina_mermas, "💾 Respaldos": pagina_respaldos}[sec](D)


# Solo se ejecuta la sección que estás viendo (antes corrían las 7 en cada toque).
{"💰 Vender": pagina_vender, "🍳 Producir": pagina_producir, "📦 Insumos": pagina_insumos,
 "📖 Recetas": pagina_recetas, "⚙️ Más": pagina_mas}[_sel](D)
