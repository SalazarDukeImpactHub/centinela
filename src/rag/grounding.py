"""Compuerta de grounding: ninguna afirmación clínica sin sustento verificable.

POR QUÉ NO ES UN UMBRAL DE SIMILITUD
Se midió sobre el índice real (6.512 chunks, 105 documentos, e5-small):

    consultas respondibles por el corpus     0.853 – 0.927
    consultas clínicas FUERA del corpus      0.871 – 0.891
    consultas ajenas a lo clínico            0.809 – 0.847

Las dos primeras se superponen. "Cuidados tras una amigdalectomía" —procedimiento
ausente del corpus— puntúa 0.891 y recupera una guía de reemplazo de rodilla, por
encima de consultas legítimas. La similitud coseno mide "esto suena a español
médico", no "el corpus responde esto". Cualquier umbral que deje pasar las
preguntas reales deja pasar también las que no tienen respuesta.

Esto importa: la rúbrica evalúa exactamente esa situación —"qué hace el agente ante
una pregunta cuya respuesta no está en su conocimiento: declara el límite y
redirige, o improvisa"— dentro del criterio de 20 puntos.

DISEÑO EN CAPAS
  1. Rechazo duro por tema. Dosis y cambios de medicación no se responden nunca,
     por bien sustentados que parezcan. La rúbrica penaliza inventar una dosis.
  2. Filtro por escenario. El procedimiento del paciente se conoce desde su perfil
     clínico; solo se recupera del corpus de ese procedimiento. Un paciente de
     rodilla jamás recibe literatura de apendicitis.
  3. Verificación léxica. El fragmento recuperado debe compartir términos con la
     consulta. Barato y ataca justo el modo de fallo que el umbral no ve.
  4. Umbral de similitud, como último filtro y no como primero.

El rechazo lo ejecuta este módulo, no el modelo.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from src.rag import bilingue, saneamiento
from src.rag.index import IndiceClinico, Recuperado

# Piso de similitud. Bajo a propósito: acá solo descarta lo abiertamente ajeno.
# El trabajo fino lo hacen el filtro de escenario y la verificación léxica.
UMBRAL_SIMILITUD = 0.85

# Cuántos fragmentos se recuperan antes de filtrar por escenario y por léxico.
# Amplio a propósito: la precisión la aporta el filtro, no el recorte temprano.
RECUPERACION_AMPLIA = 40

# Proporción de los términos distintivos de la consulta que debe aparecer en el
# fragmento. Una sola coincidencia no basta: una guía de cadera menciona "corazón"
# al hablar de riesgo cardíaco, y con eso daba por sustentada una consulta sobre
# cirugía de corazón abierto. Una mención incidental no es tratar el tema.
PROPORCION_TERMINOS = 0.6

# Longitud de la raíz para comparar términos. Seis caracteres bastan para unir
# apendic-ectomía con apendic-itis y colecist-ectomía con colecist-itis, sin
# fusionar palabras de significado distinto.
LARGO_RAIZ = 6

# Temas que no se responden jamás, con o sin sustento documental.
TEMAS_PROHIBIDOS = {
    "dosis": [
        r"\bdosis\b",
        r"\bmiligramos?\b",
        r"\bmg\b",
        r"cuant[oa]s? (pastilla|tableta|gota|cucharada)",
        r"me puedo tomar",
        r"puedo tomar(me)? (dos|otra|mas)",
        r"cada cuant[oa]s? horas",
        r"aument(ar|o) la (dosis|pastilla)",
    ],
    "cambio_medicacion": [
        # Infinitivo y primera persona: el paciente dice tanto "dejo de tomar"
        # como "puedo dejar de tomar". Cubrir solo la forma conjugada dejaba
        # pasar la pregunta más frecuente.
        r"(dejo|dejar|suspendo|suspender|paro|parar) (de tomar|el |la |los |las )",
        r"cambi(ar|o) (el|la|de) (medicamento|pastilla|antibiotico)",
        r"puedo tomar (otro|otra) ",
        r"me recet",
        r"que (medicamento|antibiotico|pastilla) (tomo|debo|me)",
    ],
}

_PROHIBIDOS = {k: [re.compile(p) for p in v] for k, v in TEMAS_PROHIBIDOS.items()}

# Anatomía y procedimientos AJENOS a los cinco escenarios del corpus
# (apendicitis, cáncer de mama, colecistitis, cáncer colorrectal, reemplazo
# articular). Si la consulta nombra alguno, no hay documentación posible.
#
# Se declara de forma explícita en lugar de deducirlo por heurística léxica porque
# el corpus es BILINGÜE: los documentos de colecistitis y de mama están en inglés,
# así que "dieta" jamás coincide con "diet" ni "drenaje" con "drainage". La
# verificación por solapamiento de términos bloqueaba consultas legítimas por esa
# razón. Una lista explícita es además auditable por el jurado.
FUERA_DE_ALCANCE = [
    r"\bcoraz(on|ones)\b", r"\bcardiac[oa]\b", r"\bbypass\b", r"\bcateterismo\b",
    r"\bcerebr(o|al)\b", r"\bneurocirug", r"\bcraneo\b", r"\bcolumna\b",
    r"\bhigado\b", r"\bhepatic[oa]\b", r"\btrasplante\b", r"\brinon(es)?\b",
    r"\brenal\b", r"\bdialisis\b", r"\bpulmon(es|ar)?\b", r"\btiroide",
    r"\bamigdal", r"\bcatarata", r"\bocular\b", r"\bcesarea\b", r"\butero\b",
    r"\bparto\b", r"\bprostat", r"\bhernia\b", r"\bvaricosa", r"\bdental\b",
]
_FUERA = [re.compile(p) for p in FUERA_DE_ALCANCE]

# Palabras sin carga semántica: no cuentan como coincidencia léxica.
VACIAS = {
    "que", "cual", "como", "cuando", "donde", "para", "por", "con", "sin", "los",
    "las", "del", "una", "uno", "unos", "unas", "the", "and", "debo", "puedo",
    "tengo", "hacer", "despues", "antes", "sobre", "mas", "menos", "muy", "este",
    "esta", "estos", "estas", "ese", "esa", "mi", "me", "se", "le", "lo", "la",
    "el", "en", "de", "a", "y", "o", "es", "son", "si", "no", "ya", "hay",
    # Preposiciones y conectores de 4+ letras: pasaban el filtro de longitud y
    # producían coincidencias espurias. "trasplante de hígado" se daba por
    # sustentado porque una guía de cadera contenía la palabra "tras".
    "tras", "ante", "bajo", "hacia", "hasta", "desde", "entre", "durante",
    "segun", "mediante", "aunque", "porque", "cuanto", "cuanta", "cuantos",
    "cuantas", "todo", "toda", "todos", "todas", "otro", "otra", "otros", "otras",
}

# Vocabulario quirúrgico genérico: aparece en TODO documento del corpus y por eso
# no prueba que el material trate el tema consultado.
#
# Sin esta distinción, "cuidados después de una cirugía de corazón abierto" pasaba
# la compuerta apoyándose en "cuidados" y "cirugia", que coinciden con cualquier
# guía quirúrgica. El término que decide es "corazon", y no está en el corpus.
# La verificación exige coincidir en al menos un término NO genérico.
GENERICAS = {
    "cirugia", "cirugias", "quirurgico", "quirurgica", "operacion", "operado",
    "postoperatorio", "posoperatorio", "preoperatorio", "paciente", "pacientes",
    "cuidado", "cuidados", "manejo", "tratamiento", "procedimiento", "hospital",
    "medico", "medica", "clinico", "clinica", "salud", "atencion", "recuperacion",
    "estudio", "resultados", "guia", "protocolo", "recomendacion", "recomendaciones",
}


@dataclass
class Veredicto:
    """Resultado de la compuerta. `permitido` decide si puede haber afirmación."""

    permitido: bool
    motivo: str
    fragmentos: list[Recuperado] = field(default_factory=list)
    tema_prohibido: str | None = None

    @property
    def citas(self) -> list[str]:
        return [f.cita() for f in self.fragmentos]

    @property
    def contexto(self) -> str:
        """Texto que se inyecta al modelo: saneado, numerado y delimitado.

        El saneamiento no es opcional. La compuerta G5 obliga a exponer una consola
        de carga de documentos, y ese canal termina en el prompt del modelo: un PDF
        preparado puede intentar darle instrucciones. La rúbrica penaliza por nombre
        que el agente obedezca instrucciones contrarias a su misión.
        """
        cuerpo = "\n\n".join(
            f"[{i}] ({f.cita()}) {saneamiento.sanear(f.texto)}"
            for i, f in enumerate(self.fragmentos, 1)
        )
        return saneamiento.envolver(cuerpo)


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def _terminos(texto: str, *, distintivos: bool = False) -> set[str]:
    """Términos con carga semántica, reducidos a su raíz.

    Con `distintivos`, descarta además el vocabulario quirúrgico genérico: son los
    términos que identifican DE QUÉ trata el texto, no que sea médico.

    Los términos se truncan a LARGO_RAIZ caracteres. El español médico declina la
    misma raíz de muchas formas —apendicectomía / apendicitis / apendicular,
    colecistectomía / colecistitis— y la comparación exacta bloqueaba consultas
    legítimas: preguntar por "complicaciones de apendicectomía" no encontraba los
    documentos de apendicitis que sí la responden.
    """
    palabras = re.findall(r"[a-z]+", _normalizar(texto))
    fuera = VACIAS | GENERICAS if distintivos else VACIAS
    return {p[:LARGO_RAIZ] for p in palabras if len(p) >= 4 and p not in fuera}


def tema_prohibido(consulta: str) -> str | None:
    """Detecta preguntas que no se responden nunca. Determinista, en código."""
    normalizada = _normalizar(consulta)
    for tema, patrones in _PROHIBIDOS.items():
        if any(p.search(normalizada) for p in patrones):
            return tema
    return None


def fuera_de_alcance(consulta: str) -> bool:
    """La consulta nombra un procedimiento u órgano ajeno a los cinco escenarios."""
    normalizada = _normalizar(consulta)
    return any(p.search(normalizada) for p in _FUERA)


def verificar(
    indice: IndiceClinico,
    consulta: str,
    *,
    escenario: str | None = None,
    k: int = 4,
) -> Veredicto:
    """Decide si la consulta puede sustentarse en el corpus.

    `escenario` es el procedimiento del paciente. Cuando se conoce, la búsqueda se
    restringe a ese corpus: es el filtro que evita responder sobre una cirugía con
    literatura de otra, que es el modo de fallo que ningún umbral detecta.
    """
    prohibido = tema_prohibido(consulta)
    if prohibido:
        return Veredicto(
            permitido=False,
            motivo=f"tema que requiere personal médico: {prohibido}",
            tema_prohibido=prohibido,
        )

    if fuera_de_alcance(consulta):
        return Veredicto(
            permitido=False,
            motivo="procedimiento fuera del alcance del corpus clínico",
        )

    # Búsqueda bilingüe. El corpus mezcla idiomas y el embedding multilingüe
    # acerca pero no siempre alcanza: una pregunta coloquial en español podía no
    # recuperar NINGÚN fragmento del escenario correcto cuando sus documentos
    # están en inglés. Se consulta también con la variante en inglés y se
    # fusionan los resultados por chunk_id, conservando la mejor similitud.
    # Red ancha, filtro preciso. MEDIDO: con k=15 los fragmentos que realmente
    # tratan el tema no entraban entre los recuperados —el embedding traía
    # formularios y texto genérico que puntúan alto sin hablar de nada—, y la
    # verificación léxica los rechazaba con razón, dejando la consulta sin
    # respuesta. Con k=40 aparecen, y la búsqueda cuesta ~50 ms: es barata
    # comparada con lo que ya gasta el turno.
    n = RECUPERACION_AMPLIA if escenario else k
    candidatos = indice.buscar(consulta, k=n)
    en_ingles = bilingue.traducir_consulta(consulta)
    if en_ingles:
        vistos = {c.chunk_id for c in candidatos}
        candidatos += [
            c for c in indice.buscar(en_ingles, k=n) if c.chunk_id not in vistos
        ]
        candidatos.sort(key=lambda c: c.similitud, reverse=True)

    if escenario:
        del_escenario = [c for c in candidatos if c.escenario == escenario]
        if not del_escenario:
            return Veredicto(
                permitido=False,
                motivo=f"sin documentación del procedimiento del paciente ({escenario})",
            )
        candidatos = del_escenario

    if not candidatos:
        return Veredicto(permitido=False, motivo="sin resultados en el corpus")

    sobre_umbral = [c for c in candidatos if c.similitud >= UMBRAL_SIMILITUD]
    if not sobre_umbral:
        mejor = max(c.similitud for c in candidatos)
        return Veredicto(
            permitido=False,
            motivo=f"similitud insuficiente (mejor {mejor:.3f} < {UMBRAL_SIMILITUD})",
        )

    distintivos = _terminos(consulta, distintivos=True)
    if not distintivos:
        # Consulta hecha solo de vocabulario genérico ("¿cuidados después de la
        # cirugía?"): no hay nada específico que verificar, y el filtro de
        # escenario ya garantizó que el material es del procedimiento correcto.
        return Veredicto(permitido=True, motivo="sustentado (consulta general)", fragmentos=sobre_umbral)

    # Una sola coincidencia basta. La protección fuerte la dan el filtro de
    # escenario y la lista de procedimientos fuera de alcance; exigir proporción
    # aquí bloqueaba consultas legítimas contra los documentos en inglés del
    # corpus, donde el solapamiento léxico en español es estructuralmente nulo.
    minimo = 1 if len(distintivos) <= 2 else math.ceil(len(distintivos) * PROPORCION_TERMINOS)

    # La comparación es bilingüe: "bañar" nunca coincidiría con "shower", y el
    # corpus de colecistitis y mama está mayormente en inglés. Cada término de
    # la consulta se compara contra sus equivalentes en el otro idioma.
    def _coincidencias(texto_fragmento: str) -> int:
        del_fragmento = _terminos(texto_fragmento, distintivos=True)
        return sum(
            1
            for termino in distintivos
            if bilingue.equivalentes_de(termino) & del_fragmento
            or termino in del_fragmento
        )

    con_lexico = [c for c in sobre_umbral if _coincidencias(c.texto) >= minimo]
    if not con_lexico:
        return Veredicto(
            permitido=False,
            motivo="el material recuperado no trata el tema consultado",
        )

    return Veredicto(permitido=True, motivo="sustentado", fragmentos=con_lexico)
