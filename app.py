from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
import math
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


APP_TITLE = "Calculadora Jubilatoria"
ANIOS_REQUERIDOS = 35.0
EDAD_BASE = 60.0
EDAD_MINIMA = 50.0
BONIFICACION_16 = 1.4
REDUCCION_EDAD_16 = 0.4
APP_DIR = Path(__file__).resolve().parent
LOGO_1 = APP_DIR / "logo1.png"
LOGO_2 = APP_DIR / "logo2.png"
LOGO_3 = APP_DIR / "logo3.png"

TIPOS_CALCULO = (
    "Prorrateo de IPS",
    "Edad Avanzada",
    "Cierre de Cómputos",
)
TIPOS_SERVICIO = (
    "",
    "Beca",
    "Residencia",
    "Designación al 14%",
    "Designación al 16%",
    "ANSES",
    "Municipal",
    "Provincial",
    "Nacional",
    "Otro",
)


@dataclass(frozen=True)
class Periodo:
    desde: date
    hasta: date
    aportes: str
    tipo: str = ""
    observaciones: str = ""

    @property
    def dias(self) -> int:
        # Se computan ambos extremos del período declarado.
        return (self.hasta - self.desde).days + 1


@dataclass(frozen=True)
class Resultado:
    dias_14: int
    dias_16: int
    computables: float
    edad_requerida: float
    edad_actual: float
    fecha_calculo: date

    @property
    def total_14(self) -> float:
        return self.dias_14 / 365.2425

    @property
    def total_16(self) -> float:
        return self.dias_16 / 365.2425

    @property
    def cumple_edad(self) -> bool:
        return self.edad_actual >= self.edad_requerida

    @property
    def cumple_servicios(self) -> bool:
        return self.computables >= ANIOS_REQUERIDOS

    @property
    def faltante_edad(self) -> float:
        return max(0.0, self.edad_requerida - self.edad_actual)

    @property
    def faltante_computable(self) -> float:
        return max(0.0, ANIOS_REQUERIDOS - self.computables)

    @property
    def faltante_servicios_16(self) -> float:
        return self.faltante_computable / BONIFICACION_16


def edad_decimal(fecha_nacimiento: date, fecha_calculo: date) -> float:
    return (fecha_calculo - fecha_nacimiento).days / 365.2425


def anios_a_texto(anios: float) -> str:
    valor = max(0.0, anios)
    anios_enteros = math.floor(valor + 1e-9)
    meses_decimales = (valor - anios_enteros) * 12
    meses = math.floor(meses_decimales + 1e-9)
    dias = round((meses_decimales - meses) * (365.2425 / 12))

    if dias >= 30:
        meses += 1
        dias = 0
    if meses >= 12:
        anios_enteros += 1
        meses = 0

    return f"{anios_enteros} años, {meses} meses y {dias} días"


def edad_a_texto(fecha_nacimiento: date, fecha_calculo: date) -> str:
    diferencia = relativedelta(fecha_calculo, fecha_nacimiento)
    return f"{diferencia.years} años, {diferencia.months} meses y {diferencia.days} días"


def normalizar_fecha(valor: Any) -> date | None:
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    fecha = pd.to_datetime(valor, errors="coerce")
    return None if pd.isna(fecha) else fecha.date()


def leer_periodos(tabla: pd.DataFrame, fecha_calculo: date) -> tuple[list[Periodo], list[str]]:
    periodos: list[Periodo] = []
    errores: list[str] = []

    for indice, fila in tabla.iterrows():
        numero = int(indice) + 1 if isinstance(indice, int) else len(periodos) + 1

        desde = normalizar_fecha(fila.get("Desde"))
        hasta = normalizar_fecha(fila.get("Hasta"))

        if desde is None and hasta is None:
            continue

        if desde is None or hasta is None:
            errores.append(f"Fila {numero}: debe completar las dos fechas.")
            continue

        if hasta < desde:
            errores.append(
                f"Fila {numero}: la fecha 'Hasta' es anterior a 'Desde'."
            )
            continue
            
        periodos.append(
            Periodo(
                desde=desde,
                hasta=hasta,
                aportes=str(fila.get("Aportes") or "14%"),
                tipo=str(fila.get("Tipo") or ""),
                observaciones=str(fila.get("Observaciones") or ""),
            )
        )

    ordenados = sorted(periodos, key=lambda periodo: (periodo.desde, periodo.hasta))

    for anterior, actual in zip(ordenados, ordenados[1:]):
        if actual.desde <= anterior.hasta:
            errores.append(
                "Hay períodos superpuestos: "
                f"{anterior.desde:%d/%m/%Y}-{anterior.hasta:%d/%m/%Y} y "
                f"{actual.desde:%d/%m/%Y}-{actual.hasta:%d/%m/%Y}."
            )

    return ordenados, errores


def calcular(periodos: list[Periodo], fecha_nacimiento: date, fecha_calculo: date) -> Resultado:
    dias_14 = sum(periodo.dias for periodo in periodos if periodo.aportes != "16%")
    dias_16 = sum(periodo.dias for periodo in periodos if periodo.aportes == "16%")
    total_14 = dias_14 / 365.2425
    total_16 = dias_16 / 365.2425
    computables = total_14 + total_16 * BONIFICACION_16
    faltan_computables = max(0.0, ANIOS_REQUERIDOS - computables)
    total_16_proyectado = total_16 + faltan_computables / BONIFICACION_16
    edad_requerida = max(EDAD_MINIMA, EDAD_BASE - total_16_proyectado * REDUCCION_EDAD_16)

    return Resultado(
        dias_14=dias_14,
        dias_16=dias_16,
        computables=computables,
        edad_requerida=edad_requerida,
        edad_actual=edad_decimal(fecha_nacimiento, fecha_calculo),
        fecha_calculo=fecha_calculo,
    )


def fecha_estimada_servicios(resultado: Resultado) -> date | None:
    if resultado.cumple_servicios:
        return None
    faltan_16 = (ANIOS_REQUERIDOS - resultado.computables) / BONIFICACION_16
    return resultado.fecha_calculo + timedelta(days=round(faltan_16 * 365.2425))


def imagen_pdf(ruta: Path, ancho: float, alto: float) -> Image | Spacer:
    if not ruta.exists():
        return Spacer(ancho, alto)
    imagen = Image(str(ruta))
    imagen._restrictSize(ancho, alto)
    imagen.hAlign = "CENTER"
    return imagen


def generar_pdf(
    nombre: str,
    fecha_nacimiento: date,
    tipo_calculo: str,
    periodos: list[Periodo],
    resultado: Resultado,
) -> bytes:
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Informe Jubilatorio",
        author=APP_TITLE,
    )
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle(
        "TituloInforme",
        parent=estilos["Title"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#17324D"),
        spaceAfter=12,
    )
    elementos = [
    Paragraph("INFORME JUBILATORIO", titulo),
    Paragraph(f"<b>Nombre:</b> {nombre or 'No informado'}", estilos["Normal"]),
    Paragraph(f"<b>Fecha de nacimiento:</b> {fecha_nacimiento:%d/%m/%Y}", estilos["Normal"]),
    Paragraph(f"<b>Fecha de cálculo:</b> {resultado.fecha_calculo:%d/%m/%Y}", estilos["Normal"]),
    Paragraph(f"<b>Tipo de cálculo:</b> {tipo_calculo}", estilos["Normal"]),
    Spacer(1, 10),
]

    datos = [["Desde", "Hasta", "Aportes", "Tipo", "Observaciones"]]
    datos.extend(
        [
            periodo.desde.strftime("%d/%m/%Y"),
            periodo.hasta.strftime("%d/%m/%Y"),
            periodo.aportes,
            periodo.tipo,
            periodo.observaciones,
        ]
        for periodo in periodos
    )
    tabla = Table(datos, repeatRows=1, colWidths=[25 * mm, 25 * mm, 18 * mm, 42 * mm, 60 * mm])
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5F7")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elementos.extend([tabla, Spacer(1, 12)])

    resumen = [
        ["Concepto", "Resultado"],
        ["Servicios al 14%", anios_a_texto(resultado.total_14)],
        ["Servicios al 16%", anios_a_texto(resultado.total_16)],
        ["Servicios computables", anios_a_texto(resultado.computables)],
        ["Edad actual", edad_a_texto(fecha_nacimiento, resultado.fecha_calculo)],
        ["Edad requerida proyectada", anios_a_texto(resultado.edad_requerida)],

        [
            "Requisito de edad",
            "✅ CUMPLE"
            if resultado.cumple_edad
            else f"❌ NO CUMPLE - Le faltan {anios_a_texto(resultado.faltante_edad)}"
        ],
        [
            "Requisito de servicios",
            "✅ CUMPLE"
            if resultado.cumple_servicios
            else f"❌ NO CUMPLE - Le faltan {anios_a_texto(resultado.faltante_servicios_16)} al 16%"
        ],

        ["Equivalente faltante con aportes al 16%", anios_a_texto(resultado.faltante_servicios_16)],
]

    tabla_resumen = Table(resumen, colWidths=[65 * mm, 105 * mm])
    tabla_resumen.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#F2F5F7")),
            ]
        )
    )
    if resultado.cumple_edad and resultado.cumple_servicios:
        conclusion = "✅ REÚNE LOS REQUISITOS CALCULADOS PARA INICIAR EL TRÁMITE JUBILATORIO."
    else:
        conclusion = "❌ NO REÚNE AÚN TODOS LOS REQUISITOS."

    if not resultado.cumple_edad:
        conclusion += f"<br/>• Edad faltante: {anios_a_texto(resultado.faltante_edad)}"

    if not resultado.cumple_servicios:
        conclusion += (
        f"<br/>• Servicios faltantes al 16%: "
        f"{anios_a_texto(resultado.faltante_servicios_16)}"
    )
    elementos.extend(
        [
            tabla_resumen,
            Spacer(1, 12),
            Paragraph(
                conclusion,
                estilos["Heading2"],
            ),
            Spacer(1, 10),
            Paragraph(
                "Resultado orientativo sujeto a la normativa vigente y a la validación del organismo previsional competente.",
                estilos["Italic"],
            ),
        ]
    )
    documento.build(elementos)
    return buffer.getvalue()

st.set_page_config(page_title=APP_TITLE, page_icon="📋", layout="wide")
st.title("🏛️ Calculadora Jubilatoria")
st.caption("Régimen IPS - Provincia de Buenos Aires")

with st.form("formulario_calculo"):
    col_datos, col_calculo = st.columns(2)

    with col_datos:
        nombre = st.text_input("Nombre y apellido")

        fecha_nacimiento_txt = st.text_input(
            "Fecha de nacimiento (dd/mm/aaaa)",
            value="01/01/1980"
        )

    with col_calculo:
        tipo_calculo = st.selectbox(
            "Tipo de cálculo",
            TIPOS_CALCULO
        )

        fecha_calculo_txt = st.text_input(
            "Fecha de cálculo (dd/mm/aaaa)",
            value=date.today().strftime("%d/%m/%Y")
        )
    st.subheader("Servicios")
    st.caption("Agregue un renglón por cada período. Los días inicial y final se computan.")
    base = pd.DataFrame(
        {
            "Desde": pd.Series([None], dtype="datetime64[ns]"),
            "Hasta": pd.Series([None], dtype="datetime64[ns]"),
            "Aportes": ["14%"],
            "Tipo": [""],
            "Observaciones": [""],
        }
    )
    tabla_editada = st.data_editor(
        base,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "Desde": st.column_config.DateColumn("Desde", format="DD/MM/YYYY"),
            "Hasta": st.column_config.DateColumn("Hasta", format="DD/MM/YYYY"),
            "Aportes": st.column_config.SelectboxColumn("Aportes", options=["14%", "16%"], required=True),
            "Tipo": st.column_config.SelectboxColumn("Tipo", options=TIPOS_SERVICIO),
            "Observaciones": st.column_config.TextColumn("Observaciones", max_chars=250),
        },
        key="tabla_servicios",
    )
    calcular_pulsado = st.form_submit_button("Calcular", type="primary", width="stretch")
if calcular_pulsado:
    
    try:
        fecha_nacimiento = datetime.strptime(
        fecha_nacimiento_txt,
        "%d/%m/%Y"
        ).date()

        fecha_calculo = datetime.strptime(
        fecha_calculo_txt,
        "%d/%m/%Y"
        ).date()

    except ValueError:
        st.error(
            "Las fechas deben ingresarse en formato dd/mm/aaaa y ser válidas."
        )
        st.stop()

    if not nombre.strip():
    st.error("Debe ingresar nombre y apellido.")
    st.stop()

    if not fecha_nacimiento_txt.strip():
        st.error("Debe ingresar la fecha de nacimiento.")
        st.stop()
        if tipo_calculo != "Prorrateo de IPS":
        st.error(
            f"El régimen '{tipo_calculo}' todavía no tiene una fórmula configurada. "
            "No se generó un resultado para evitar una estimación incorrecta."
        )
        st.session_state.pop("informe", None)
    elif fecha_nacimiento >= fecha_calculo:
        st.error("La fecha de nacimiento debe ser anterior a la fecha de cálculo.")
        st.session_state.pop("informe", None)
    else:
        periodos, errores = leer_periodos(tabla_editada, fecha_calculo)
        if not periodos:
            errores.append("Debe ingresar al menos un período de servicios válido.")
        if errores:
            st.error("No se pudo calcular. Revise los datos ingresados:")
            for error in dict.fromkeys(errores):
                st.markdown(f"- {error}")
            st.session_state.pop("informe", None)
        else:
            st.session_state["informe"] = {
                "nombre": nombre.strip(),
                "fecha_nacimiento": fecha_nacimiento,
                "tipo_calculo": tipo_calculo,
                "periodos": periodos,
                "resultado": calcular(periodos, fecha_nacimiento, fecha_calculo),
            }

if "informe" in st.session_state:
    informe = st.session_state["informe"]
    resultado: Resultado = informe["resultado"]
    st.divider()
    st.subheader("Informe Jubilatorio")

    metrica_1, metrica_2, metrica_3, metrica_4 = st.columns(4)
    metrica_1.metric("Servicios 14%", anios_a_texto(resultado.total_14))
    metrica_2.metric("Servicios 16%", anios_a_texto(resultado.total_16))
    metrica_3.metric("Computables", anios_a_texto(resultado.computables))
    metrica_4.metric("Edad requerida", anios_a_texto(resultado.edad_requerida))

    col_edad, col_servicios = st.columns(2)
    with col_edad:
        st.markdown("**Requisito de edad**")
        st.write(f"Edad actual: {edad_a_texto(informe['fecha_nacimiento'], resultado.fecha_calculo)}")
        st.write(f"Falta para cumplir: **{anios_a_texto(resultado.faltante_edad)}**")
        if resultado.cumple_edad:
            st.success("Cumple el requisito de edad.")
        else:
            st.warning(f"Faltan aproximadamente {anios_a_texto(resultado.faltante_edad)}.")
    with col_servicios:
        st.markdown("**Requisito de servicios**")
        st.write(f"Faltan computables: **{anios_a_texto(resultado.faltante_computable)}**")
        st.write(f"Equivalente con continuidad al 16%: **{anios_a_texto(resultado.faltante_servicios_16)}**")
        if resultado.cumple_servicios:
            st.success("Cumple el requisito de servicios.")
        else:
            estimada = fecha_estimada_servicios(resultado)
            st.warning(f"Faltan {anios_a_texto(resultado.faltante_servicios_16)} de servicios al 16%.")
            st.info(f"Fecha estimada con continuidad al 16%: {estimada:%d/%m/%Y}")

    if resultado.cumple_edad and resultado.cumple_servicios:
        st.success("Reúne los requisitos calculados para iniciar el trámite.", icon="✅")
    else:
        st.error("Todavía no reúne todos los requisitos calculados.", icon="❌")

    pdf = generar_pdf(
        informe["nombre"],
        informe["fecha_nacimiento"],
        informe["tipo_calculo"],
        informe["periodos"],
        resultado,
    )
    st.download_button(
        "Descargar informe PDF",
        data=pdf,
        file_name=f"Informe_Jubilatorio_{resultado.fecha_calculo:%Y%m%d}.pdf",
        mime="application/pdf",
        width="stretch",
    )

with st.expander("Criterios utilizados"):
    st.markdown(
        f"""
        - Servicios requeridos: **{ANIOS_REQUERIDOS:g} años computables**.
        - Los servicios al 16% se multiplican por **{BONIFICACION_16:g}**.
        - Edad base: **{EDAD_BASE:g} años**; reducción proyectada: **{REDUCCION_EDAD_16:g}** por cada año al 16%.
        - Edad mínima aplicada: **{EDAD_MINIMA:g} años**.
        - La proyección supone continuidad futura de servicios al 16%.
        - El resultado es orientativo y debe validarse con el organismo previsional competente.
        """
    )
st.markdown("---")

st.caption(
    "Desarrollado por Narela Berenice Altamirano | "
    "RRHH Hospital Virgen del Carmen | "
    "Versión 1.0"
)
