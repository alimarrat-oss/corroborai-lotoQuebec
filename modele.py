"""Structures de données partagées : Constat (une ligne du rapport) et Signal (preuve)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

EXPLICATIF = "explicatif"   # pourrait justifier l'écart
AGGRAVANT = "aggravant"     # renforce l'hypothèse d'une erreur
CONTEXTE = "contexte"       # information utile, neutre


@dataclass
class Signal:
    code: str
    nature: str
    description: str

    def court(self) -> str:
        return f"[{self.code}] {self.description}"


@dataclass
class Constat:
    id: str
    matricule: str
    affectation: str
    cle: str                         # clé stable pour la rétroaction experte
    ligne_source: int | None
    ligne_cible: int | None
    champ_cible: str
    champs_source: str
    libelle: str
    regle: str
    valeur_source: str
    valeur_attendue: str
    valeur_cible: str
    verdict: str | None = None
    origine: str = ""
    confiance: float = 1.0
    justification: str = ""
    action: str = ""
    trace: list[str] = field(default_factory=list)
    normalisations: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    signaux: list[Signal] = field(default_factory=list)
    impact: float = 0.5
    priorite: float = 0.0
    ambigu: bool = False
    avis_ia: dict[str, Any] = field(default_factory=dict)
    # objets de travail (non exportés)
    _attendu: Any = field(default=None, repr=False)
    _cible: Any = field(default=None, repr=False)
    _calculable: bool = field(default=True, repr=False)
    _ctx: Any = field(default=None, repr=False)
    _cib_row: Any = field(default=None, repr=False)

    @property
    def signature(self) -> str:
        """Signature d'un cas : champ + types de signaux (sert à regrouper et à apprendre)."""
        codes = sorted({s.code for s in self.signaux if s.nature != CONTEXTE})
        return f"{self.champ_cible}|{'+'.join(codes) or 'aucun-signal'}"

    def vers_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("_")}
        d["signaux"] = [s.court() for s in self.signaux]
        d["avis_ia"] = dict(self.avis_ia)
        return d
