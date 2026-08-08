"""The toy corpus: facts about the fictional city of Riverbend.

English and Spanish documents are peers with their own identifiers. The
Spanish corpus is not a translation pipeline bolted onto the English one;
retrieval runs per language against that language's documents.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Doc:
    id: str
    language: str
    keywords: tuple[str, ...]
    statement: str


CORPUS: tuple[Doc, ...] = (
    Doc(
        "RB-001",
        "en",
        ("library",),
        "Riverbend Central Library is open Monday through Saturday, 9:00 to 18:00.",
    ),
    Doc(
        "RB-002",
        "en",
        ("dog license", "dog"),
        "A Riverbend dog license costs 18 dollars per year and renews each January.",
    ),
    Doc(
        "RB-003",
        "en",
        ("bulky", "mattress", "furniture"),
        "Riverbend collects bulky items by appointment; each household gets two free pickups per year.",
    ),
    Doc(
        "RB-004",
        "en",
        ("transit", "bus", "day pass"),
        "A Riverbend Transit day pass costs 5 dollars and covers all bus lines until midnight.",
    ),
    Doc(
        "RB-005",
        "en",
        ("knife", "knives", "blade", "blades"),
        "Old kitchen knives and other blades are accepted at the Riverbend Recycling Center wrapped and inside a rigid container.",
    ),
    Doc(
        "RB-006",
        "en",
        ("park", "picnic", "event permit"),
        "A permit for a private event in a Riverbend park costs 40 dollars and must be requested 14 days ahead.",
    ),
    Doc(
        "RB-007",
        "en",
        ("recycling",),
        "The Riverbend Recycling Center is open Tuesday through Sunday, 8:00 to 16:00.",
    ),
    Doc(
        "RB-008",
        "en",
        ("pool", "swim"),
        "The Riverbend Aquatic Center pool offers open swim daily from 12:00 to 15:00 in summer.",
    ),
    Doc(
        "RB-101",
        "es",
        ("biblioteca",),
        "La Biblioteca Central de Riverbend abre de lunes a sábado, de 9:00 a 18:00.",
    ),
    Doc(
        "RB-102",
        "es",
        ("licencia de perro", "perro"),
        "La licencia de perro en Riverbend cuesta 18 dólares al año y se renueva cada enero.",
    ),
    Doc(
        "RB-103",
        "es",
        ("voluminosos", "colchón", "muebles"),
        "Riverbend recoge artículos voluminosos con cita previa; cada hogar tiene dos recolecciones gratuitas al año.",
    ),
    Doc(
        "RB-104",
        "es",
        ("transporte", "autobús", "pase diario"),
        "El pase diario de Riverbend Transit cuesta 5 dólares y cubre todas las líneas de autobús hasta la medianoche.",
    ),
    Doc(
        "RB-105",
        "es",
        ("cuchillos", "cuchillo", "hojas"),
        "Los cuchillos de cocina viejos y otras hojas se aceptan en el Centro de Reciclaje de Riverbend envueltos y dentro de un recipiente rígido.",
    ),
    Doc(
        "RB-106",
        "es",
        ("parque", "picnic", "permiso de evento"),
        "Un permiso para un evento privado en un parque de Riverbend cuesta 40 dólares y debe solicitarse con 14 días de anticipación.",
    ),
    Doc(
        "RB-107",
        "es",
        ("reciclaje",),
        "El Centro de Reciclaje de Riverbend abre de martes a domingo, de 8:00 a 16:00.",
    ),
    Doc(
        "RB-108",
        "es",
        ("piscina", "nadar"),
        "La piscina del Centro Acuático de Riverbend ofrece nado libre todos los días de 12:00 a 15:00 en verano.",
    ),
)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def retrieve(prompt: str, language: str) -> tuple[Doc, ...]:
    """Return the documents of this language whose keywords appear in the prompt."""
    haystack = normalize(prompt)
    return tuple(
        doc
        for doc in CORPUS
        if doc.language == language
        and any(normalize(keyword) in haystack for keyword in doc.keywords)
    )
