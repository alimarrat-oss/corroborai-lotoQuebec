"""Appariement des affectations source ↔ cible.

Un employé peut avoir plusieurs affectations (structure répétitive) : la clé
n'est donc pas le seul matricule. Pour chaque matricule, on résout une
affectation optimale (algorithme hongrois) selon un score de similarité
explicable : emploi identique, type d'affectation cohérent, date de début et
unité administrative identiques.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from .normalisation import vers_bool, vers_date, vers_nombre

POIDS = {"emploi": 4, "type": 3, "date_debut": 2, "unite": 1}
SEUIL = 4  # score minimal pour accepter une paire


@dataclass
class Paire:
    matricule: str
    i_src: int | None
    i_cib: int | None
    score: int
    details: str


def _type_cible(r: pd.Series) -> str:
    p, t = vers_bool(r.get("isPrimaryAssignment")), vers_bool(r.get("isTemporaryAssignment"))
    return "P" if p else ("A" if t else "S")


def _score(s: pd.Series, c: pd.Series) -> tuple[int, list[str]]:
    sc, det = 0, []
    if vers_nombre(s["CodeEmploi"]) == vers_nombre(c["positionId"]):
        sc += POIDS["emploi"]; det.append("emploi")
    if str(s["TypeAffectation"]).upper() == _type_cible(c):
        sc += POIDS["type"]; det.append("type")
    if vers_date(s["DateEntréePoste"]) == vers_date(c["assignmentStartDate"]):
        sc += POIDS["date_debut"]; det.append("date début")
    if vers_nombre(s["CodeDirection"]) == vers_nombre(c["divisionId"]):
        sc += POIDS["unite"]; det.append("unité")
    return sc, det


def apparier(source: pd.DataFrame, cible: pd.DataFrame) -> list[Paire]:
    paires: list[Paire] = []
    mats = list(dict.fromkeys(list(source["Matricule"].dropna()) + list(cible["personId"].dropna())))
    for m in mats:
        S = source.index[source["Matricule"] == m].tolist()
        C = cible.index[cible["personId"] == m].tolist()
        if not S or not C:
            for i in S:
                paires.append(Paire(m, i, None, 0, "Matricule absent de la cible"))
            for j in C:
                paires.append(Paire(m, None, j, 0, "Matricule absent de la source"))
            continue
        scores = np.zeros((len(S), len(C)), dtype=int)
        dets = {}
        for a, i in enumerate(S):
            for b, j in enumerate(C):
                scores[a, b], dets[(a, b)] = _score(source.loc[i], cible.loc[j])
        lignes, cols = linear_sum_assignment(-scores)
        pris_s, pris_c = set(), set()
        for a, b in zip(lignes, cols):
            if scores[a, b] >= SEUIL:
                paires.append(Paire(m, S[a], C[b], int(scores[a, b]),
                                    "Critères concordants : " + ", ".join(dets[(a, b)])))
                pris_s.add(a); pris_c.add(b)
        for a, i in enumerate(S):
            if a not in pris_s:
                paires.append(Paire(m, i, None, 0, "Aucune affectation cible correspondante"))
        for b, j in enumerate(C):
            if b not in pris_c:
                paires.append(Paire(m, None, j, 0, "Aucune affectation source correspondante"))
    return paires
