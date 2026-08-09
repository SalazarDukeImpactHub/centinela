"""Puente español↔inglés para el corpus bilingüe. Determinista, en código.

El corpus del reto mezcla idiomas: los documentos de colecistitis y de la
carpeta `breast_cancer` están mayormente en inglés, y los pacientes preguntan en
español coloquial. La consecuencia se midió: **5 de 8 consultas clínicas
legítimas quedaban bloqueadas** por la compuerta, con el agente diciendo "no lo
sé" sobre material que sí tenía.

Dos capas lo necesitan:

  1. La BÚSQUEDA. El embedding multilingüe acerca los idiomas, pero no siempre
     lo suficiente: una pregunta coloquial en español puede no recuperar ningún
     fragmento del escenario correcto. Se consulta también con la variante en
     inglés y se fusionan los resultados.

  2. La VERIFICACIÓN LÉXICA. Exige compartir términos con el fragmento, y
     "bañar" nunca va a coincidir con "shower". El solapamiento se calcula sobre
     el término y sus equivalentes.

Se resuelve con un diccionario y no con un traductor automático a propósito: es
vocabulario clínico cerrado, cabe en una página, no agrega latencia, y sobre
todo **es auditable** — un jurado puede leer exactamente qué se consideró
equivalente a qué. Un traductor sería una caja negra más en la cadena que
sustenta afirmaciones clínicas.
"""

from __future__ import annotations

import re
import unicodedata

# Vocabulario clínico posoperatorio del corpus. La clave es la raíz en español
# —para tolerar plurales y géneros— y el valor son sus equivalentes en inglés.
EQUIVALENTES: dict[str, tuple[str, ...]] = {
    # Procedimientos y anatomía
    "vesicula": ("gallbladder", "cholecyst", "biliary"),
    "colecistectomia": ("cholecystectomy", "gallbladder removal"),
    "apendice": ("appendix", "appendice"),
    "apendicectomia": ("appendectomy", "appendicectomy"),
    "colon": ("colon", "colorectal", "bowel"),
    "colectomia": ("colectomy", "bowel resection"),
    "mama": ("breast", "mammary"),
    "mastectomia": ("mastectomy",),
    "rodilla": ("knee",),
    "cadera": ("hip",),
    "articulacion": ("joint", "arthroplasty"),
    "protesis": ("prosthesis", "implant", "arthroplasty"),
    # Herida y complicaciones
    "herida": ("wound", "incision", "surgical site"),
    "incision": ("incision", "wound"),
    "cicatriz": ("scar", "healing", "cicatri"),
    "infeccion": ("infection", "infected", "sepsis"),
    "secrecion": ("discharge", "drainage", "exudate", "purulent"),
    "pus": ("pus", "purulent", "suppurat"),
    "enrojecimiento": ("redness", "erythema", "red"),
    "hinchazon": ("swelling", "edema", "swollen", "inflammation"),
    "sangrado": ("bleeding", "hemorrhage", "blood"),
    "drenaje": ("drain", "drainage"),
    "punto": ("stitch", "suture", "staple"),
    "sutura": ("suture", "stitch"),
    # Síntomas
    "fiebre": ("fever", "febrile", "pyrexia", "temperature"),
    "dolor": ("pain", "ache", "discomfort", "analgesi"),
    "nausea": ("nausea", "nauseated"),
    "vomito": ("vomiting", "emesis"),
    "escalofrio": ("chills", "shivering", "rigors"),
    # Cuidados y recuperación
    "cuidado": ("care", "management"),
    "bañar": ("shower", "bathe", "bathing", "wash"),
    "ducha": ("shower", "showering"),
    "comer": ("eat", "eating", "diet", "oral intake", "feeding"),
    "dieta": ("diet", "dietary", "nutrition", "food"),
    "alimentacion": ("nutrition", "feeding", "diet"),
    "caminar": ("walk", "walking", "ambulat", "mobiliz"),
    "movilidad": ("mobility", "mobiliz", "ambulat"),
    "ejercicio": ("exercise", "activity", "rehabilitat"),
    "levantar": ("lift", "lifting", "weight"),
    "peso": ("weight", "load", "bearing"),
    "fuerza": ("strain", "exertion", "lifting"),
    "trabajar": ("work", "return to work", "occupational"),
    "conducir": ("driv", "drive"),
    "reposo": ("rest", "recovery"),
    "recuperacion": ("recovery", "recuperation", "rehabilitat"),
    "medicamento": ("medication", "drug", "analgesi"),
    "antibiotico": ("antibiotic", "antimicrobial"),
    "control": ("follow-up", "followup", "checkup"),
    "cita": ("appointment", "follow-up"),
    "alta": ("discharge",),
    "complicacion": ("complication", "adverse"),
}

def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


# Las claves se normalizan al cargar: la búsqueda compara sobre texto sin tildes
# y "bañar" nunca habría encontrado su entrada. Escribir el diccionario en
# español correcto y normalizarlo acá evita tener que recordar la regla.
EQUIVALENTES = {_normalizar(k): v for k, v in EQUIVALENTES.items()}

# Índice inverso: dado un término en inglés, su equivalente en español. Permite
# que la verificación funcione en las dos direcciones.
_INVERSO: dict[str, set[str]] = {}
for _es, _ens in EQUIVALENTES.items():
    for _en in _ens:
        _INVERSO.setdefault(_en, set()).add(_es)


def equivalentes_de(termino: str) -> set[str]:
    """Todas las formas equivalentes de un término, en ambos idiomas.

    Incluye el término original: un fragmento en español debe seguir
    coincidiendo consigo mismo.
    """
    t = _normalizar(termino)
    formas = {t}
    # Coincidencia por raíz para tolerar plurales y géneros: "heridas" -> "herida".
    for es, ens in EQUIVALENTES.items():
        if t.startswith(es[:6]) or es.startswith(t[:6]):
            formas.add(es)
            formas.update(ens)
    formas.update(_INVERSO.get(t, set()))
    return formas


def expandir(texto: str) -> set[str]:
    """Términos del texto más todos sus equivalentes en el otro idioma."""
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    formas: set[str] = set()
    for palabra in palabras:
        if len(palabra) >= 4:
            formas |= equivalentes_de(palabra)
    return formas


# Contexto que se añade a la consulta traducida. MEDIDO: el término suelto
# ("shower") recupera el mejor fragmento de colecistitis con 0,816 —bajo el
# umbral—, mientras que la misma búsqueda enmarcada como bolsa clínica
# ("postoperative shower ... wound care instructions") sube a 0,870 y pasa.
#
# La causa es cómo se escribieron los documentos: son guías clínicas, no
# conversaciones. Una pregunta en lenguaje natural —"when can I shower after
# surgery"— puntúa PEOR que la terminología del propio corpus. La consulta se
# formula como habla el documento, no como habla el paciente.
MARCO_CLINICO = ("postoperative", "patient care instructions")


def traducir_consulta(texto: str) -> str | None:
    """Versión en inglés de la consulta, para buscar también en esa mitad del
    corpus. Devuelve None si no hay ningún término clínico traducible.

    No es una traducción gramatical: es una bolsa de términos clínicos enmarcada
    en el registro del corpus, que es lo que un buscador vectorial necesita.
    """
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    en: list[str] = []
    for palabra in palabras:
        if len(palabra) < 4:
            continue
        for es, ens in EQUIVALENTES.items():
            if palabra.startswith(es[:6]) or es.startswith(palabra[:6]):
                # Se toman todos los equivalentes, no solo el primero: la
                # terminología del corpus varía entre documentos y con uno solo
                # se pierde el que ese documento usa.
                en.extend(ens[:2])
                break
    if not en:
        return None
    terminos = list(dict.fromkeys(en))
    return " ".join([MARCO_CLINICO[0], *terminos, MARCO_CLINICO[1]])
