"""Extracción de texto del corpus clínico en PDF.

Responsabilidad única: PDF → texto por página, con detección de los defectos
conocidos del corpus (documentos sin capa de texto, duplicados).

Defectos documentados por el kit oficial:
  - Un PDF de Appendicitis/ está escaneado SIN capa de texto.
  - Hay documentos repetidos entre carpetas.
  - Dos nombres de carpeta contienen espacios.

La deduplicación ocurre en dos niveles, porque el hash binario no alcanza:
  1. Hash SHA-256 del archivo — mismo archivo, distinto nombre o carpeta.
  2. Hash del texto normalizado — mismo documento reeditado (p. ej. el mismo
     paper con y sin encabezados de la revista). El corpus tiene un caso real:
     "Postoperative Pain Management in Total Knee Arthroplasty" aparece dos veces
     con 1.0 de similitud de contenido y distinto tamaño de archivo.

El segundo nivel importa para el RAG: la misma afirmación clínica recuperada dos
veces infla artificialmente la confianza del grounding.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

# Un PDF con capa de texto real supera holgadamente este umbral por página.
# Por debajo, es un escaneo sin OCR o un documento vacío.
MIN_CHARS_POR_PAGINA = 50

# Longitud del prefijo normalizado usado como huella de contenido.
# Suficiente para identificar un documento sin arrastrar las divergencias de
# maquetación que aparecen más adelante.
CHARS_HUELLA = 3000


@dataclass
class Pagina:
    numero: int  # 1-indexed, como lo ve un humano al citar
    texto: str


@dataclass
class Documento:
    ruta: Path
    escenario: str  # carpeta de origen: Appendicitis, breast_cancer, ...
    sha256: str
    paginas: list[Pagina] = field(default_factory=list)

    @property
    def doc_id(self) -> str:
        """Identificador estable para trazabilidad. Los 12 primeros del hash bastan."""
        return self.sha256[:12]

    @property
    def total_chars(self) -> int:
        return sum(len(p.texto) for p in self.paginas)

    @property
    def sin_capa_texto(self) -> bool:
        """True si el PDF no tiene texto extraíble (escaneo sin OCR)."""
        if not self.paginas:
            return True
        return self.total_chars / len(self.paginas) < MIN_CHARS_POR_PAGINA

    @property
    def huella_contenido(self) -> str:
        """Hash del arranque del texto normalizado — detecta el mismo documento reeditado.

        Normaliza a minúsculas y colapsa el espacio en blanco, y toma solo los
        primeros CHARS_HUELLA caracteres. El prefijo es deliberado: dos ediciones
        del mismo paper difieren en encabezados de revista y paginación repartidos
        a lo largo del documento, pero arrancan igual. Un hash del texto completo
        no las reconocería como el mismo documento.
        """
        texto = " ".join(p.texto for p in self.paginas)
        normalizado = re.sub(r"\s+", " ", texto.lower()).strip()
        return hashlib.sha256(normalizado[:CHARS_HUELLA].encode("utf-8")).hexdigest()


def _abrible(ruta: Path) -> str:
    """Ruta utilizable por el SO.

    Varios PDFs del corpus tienen nombres que superan el límite MAX_PATH de 260
    caracteres de Windows. El prefijo `\\\\?\\` lo evita, pero exige ruta absoluta,
    resuelta y con separadores nativos.
    """
    absoluta = ruta.resolve()
    if os.name == "nt" and not str(absoluta).startswith("\\\\?\\"):
        return "\\\\?\\" + str(absoluta)
    return str(absoluta)


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(_abrible(ruta), "rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def extraer(ruta: Path, escenario: str) -> Documento:
    """Extrae el texto de un PDF. No lanza si el PDF está corrupto: lo reporta vacío."""
    doc = Documento(ruta=ruta, escenario=escenario, sha256=_sha256(ruta))
    try:
        with fitz.open(_abrible(ruta)) as pdf:
            for i, pagina in enumerate(pdf, start=1):
                texto = pagina.get_text("text").strip()
                if texto:
                    doc.paginas.append(Pagina(numero=i, texto=texto))
    except Exception as exc:  # PDF corrupto o cifrado
        doc.paginas = []
        print(f"  ! no se pudo leer {ruta.name}: {exc}")
    return doc


@dataclass
class ResultadoCorpus:
    documentos: list[Documento]  # únicos, con texto extraíble
    duplicados: list[tuple[Documento, Documento]] = field(default_factory=list)
    sin_texto: list[Documento] = field(default_factory=list)

    def resumen(self) -> str:
        paginas = sum(len(d.paginas) for d in self.documentos)
        chars = sum(d.total_chars for d in self.documentos)
        return (
            f"{len(self.documentos)} documentos ingeribles · {paginas} páginas · "
            f"{chars:,} caracteres · {len(self.duplicados)} duplicados descartados · "
            f"{len(self.sin_texto)} sin capa de texto"
        )


def recorrer_corpus(raiz: Path) -> ResultadoCorpus:
    """Extrae todos los PDFs bajo raíz y separa duplicados y documentos sin texto.

    Para un mismo documento visto dos veces gana el primero en orden alfabético;
    el segundo se reporta como duplicado. Los documentos sin capa de texto se
    apartan: no aportan nada al índice vectorial y su presencia enmascararía un
    hueco de conocimiento como si fuera cobertura.
    """
    resultado = ResultadoCorpus(documentos=[])
    por_archivo: dict[str, Documento] = {}
    por_contenido: dict[str, Documento] = {}

    for ruta in sorted(raiz.rglob("*.pdf")):
        escenario = ruta.relative_to(raiz).parts[0]
        doc = extraer(ruta, escenario)

        if doc.sha256 in por_archivo:
            resultado.duplicados.append((doc, por_archivo[doc.sha256]))
            continue
        por_archivo[doc.sha256] = doc

        if doc.sin_capa_texto:
            resultado.sin_texto.append(doc)
            continue

        huella = doc.huella_contenido
        if huella in por_contenido:
            resultado.duplicados.append((doc, por_contenido[huella]))
            continue
        por_contenido[huella] = doc
        resultado.documentos.append(doc)

    return resultado
