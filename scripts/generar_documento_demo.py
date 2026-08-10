"""Genera el documento clínico de demostración para la compuerta G5.

La compuerta se verifica con "un documento de prueba que no forma parte de
ningún corpus entregado". Este es ese documento, y el tema no es casual:
**cuidados tras mastectomía**.

Es el hueco real del corpus. La carpeta `breast_cancer` del kit contiene
literatura de cáncer de cuello uterino —18 de 19 documentos mencionan cérvix,
ninguno menciona mama— mientras 8 de los 40 pacientes fueron mastectomizados.
Ver `docs/corpus-hallazgos.md`.

Eso convierte la demostración en algo mucho más fuerte que "subo un PDF y
aparece en una lista":

    1. Antes de subirlo, el agente DECLARA EL LÍMITE ante una consulta de
       mastectomía — no inventa, y tampoco cita literatura de cérvix.
    2. Se sube el documento.
    3. El agente responde citándolo, con archivo y página.
    4. Se elimina.
    5. El agente vuelve a declarar el límite.

El contenido es clínicamente correcto y está redactado como una guía de
paciente real, pero se marca de forma visible como material de demostración
para que nadie lo confunda con documentación clínica vigente.

    python scripts/generar_documento_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "docs" / "demo" / "cuidados-tras-mastectomia-DEMO.pdf"

TITULO = "Cuidados en casa después de una mastectomía"
SUBTITULO = "Guía para la paciente · Documento de DEMOSTRACIÓN"

SECCIONES: list[tuple[str, list[str]]] = [
    (
        "Cuidado del drenaje",
        [
            "Es normal salir del hospital con uno o dos drenajes. Anote todos los",
            "días cuánto líquido salió y de qué color era.",
            "El drenaje se retira cuando el débito es menor a treinta mililitros en",
            "veinticuatro horas durante dos días seguidos.",
            "Vacíe el recipiente dos veces al día y lávese las manos antes y después.",
            "Consulte si el líquido se vuelve espeso, con mal olor o cambia a un",
            "color amarillo verdoso: puede indicar infección.",
        ],
    ),
    (
        "Cuidado de la herida",
        [
            "Mantenga la herida limpia y seca durante las primeras cuarenta y ocho",
            "horas. Después puede ducharse dejando correr el agua sin frotar.",
            "No sumerja la herida en tina, piscina ni mar hasta el control médico.",
            "Un enrojecimiento leve en el borde es esperable los primeros días.",
            "Consulte el mismo día si aparece enrojecimiento que se extiende, calor",
            "en la zona, hinchazón que aumenta o cualquier secreción.",
        ],
    ),
    (
        "Movimiento del brazo",
        [
            "Empiece a mover el hombro a las cuarenta y ocho horas, con ejercicios",
            "suaves y sin forzar. Detenga cualquier movimiento que duela.",
            "No levante peso mayor a dos kilos con el brazo del lado operado",
            "durante las primeras cuatro semanas.",
            "Evite tomar la presión arterial, aplicar inyecciones o sacar sangre",
            "del brazo del lado operado.",
        ],
    ),
    (
        "Linfedema: qué vigilar",
        [
            "Si le retiraron ganglios de la axila, existe riesgo de linfedema.",
            "Consulte si nota el brazo, la mano o los dedos hinchados, con sensación",
            "de pesadez, o si la ropa y los anillos le quedan más apretados.",
            "Proteja el brazo de cortes y quemaduras: use guantes al cocinar y al",
            "trabajar en el jardín.",
        ],
    ),
    (
        "Cuándo llamar de inmediato",
        [
            "Fiebre de treinta y ocho grados o más.",
            "Escalofríos con sensación de malestar general.",
            "Secreción con mal olor o de aspecto purulento en la herida.",
            "Enrojecimiento que se extiende más allá del borde de la herida.",
            "Dolor que aumenta en vez de disminuir con el paso de los días.",
            "Sangrado que empapa el apósito.",
        ],
    ),
    (
        "Alimentación y ánimo",
        [
            "No hay una dieta especial después de una mastectomía. Coma variado,",
            "con suficiente proteína, y tome líquidos.",
            "Es frecuente sentirse triste o ansiosa las primeras semanas. Coméntelo",
            "en el control: hace parte de la recuperación y tiene apoyo disponible.",
        ],
    ),
]


def main() -> int:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("Falta PyMuPDF. Instalar con: pip install pymupdf")
        return 1

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    documento = fitz.open()
    pagina = documento.new_page()
    ancho = pagina.rect.width

    # Banda superior de advertencia: nadie debe confundir esto con documentación
    # clínica vigente, ni siquiera si el archivo circula suelto.
    pagina.draw_rect(fitz.Rect(0, 0, ancho, 34), color=None, fill=(0.86, 0.20, 0.16))
    pagina.insert_text(
        (40, 22),
        "DOCUMENTO DE DEMOSTRACIÓN — no es documentación clínica vigente",
        fontsize=10,
        fontname="Helvetica-Bold",
        color=(1, 1, 1),
    )

    y = 70
    pagina.insert_text((40, y), TITULO, fontsize=17, fontname="Helvetica-Bold")
    y += 20
    pagina.insert_text((40, y), SUBTITULO, fontsize=9.5, color=(0.35, 0.35, 0.35))
    y += 28

    for titulo, lineas in SECCIONES:
        if y > 720:
            pagina = documento.new_page()
            y = 60
        pagina.insert_text(
            (40, y), titulo, fontsize=12, fontname="Helvetica-Bold", color=(0.05, 0.28, 0.55)
        )
        y += 16
        for linea in lineas:
            if y > 760:
                pagina = documento.new_page()
                y = 60
            pagina.insert_text((48, y), linea, fontsize=10)
            y += 13.5
        y += 12

    pagina.insert_text(
        (40, min(y + 10, 780)),
        "Material generado para probar la carga de conocimiento en caliente "
        "(compuerta G5) del agente Centinela.",
        fontsize=8,
        color=(0.45, 0.45, 0.45),
    )

    documento.save(str(DESTINO))
    documento.close()
    print(f"Generado: {DESTINO.relative_to(RAIZ)}")
    print(f"  {DESTINO.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
