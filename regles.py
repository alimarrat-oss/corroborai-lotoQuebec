"""Catalogue des règles métier (niveau 2).

Chaque règle calcule la VALEUR ATTENDUE dans le système B – Temps à partir des
données du système A – RH (et des extractions complémentaires), et produit une
TRACE lisible des étapes de calcul : c'est la preuve affichée dans le rapport.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from .config import Champ, Parametres
from .normalisation import (afficher, est_vide, normaliser, sans_accents, vers_bool,
                            vers_date, vers_nombre)


@dataclass
class Contexte:
    """Tout ce qu'une règle peut consulter pour une affectation donnée."""
    src: pd.Series                       # ligne de l'extraction source
    detail: pd.DataFrame                 # historique « détail du poste » du CodePoste (trié)
    motif: pd.Series | None              # ligne « motif de situation » (jointure CodeRaisonStatut)
    params: Parametres


@dataclass
class Attendu:
    valeur: Any                          # valeur attendue normalisée
    calculable: bool = True
    trace: list[str] = field(default_factory=list)
    transforme: bool = False             # la règle transforme la valeur source (≠ copie directe)
    hypotheses: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Regle:
    id: str
    titre: str
    description: str
    calcul: Callable[[Champ, Contexte], Attendu]


# ---------------------------------------------------------------------------
def _direct(ch: Champ, ctx: Contexte) -> Attendu:
    col = ch.sources[0]
    v, codes = normaliser(ctx.src.get(col), ch.type)
    trace = [f"{col} = « {afficher(v)} » (copie directe)"]
    if v is None:
        return Attendu(None, calculable=False, trace=trace + ["Valeur source vide : attendu indéterminé"])
    return Attendu(v, trace=trace)


def _email(ch: Champ, ctx: Contexte) -> Attendu:
    pre, nom, mat = (ctx.src.get(c) for c in ("PrénomUsuel", "NomFamille", "Matricule"))
    if any(est_vide(x) for x in (pre, nom, mat)):
        return Attendu(None, calculable=False, trace=["Prénom, nom ou matricule manquant"])
    p = sans_accents(str(pre)).strip()[0]
    n = re.sub(r"[\s'’]", "", sans_accents(str(nom)))
    code3 = re.sub(r"\D", "", str(mat))[-3:]
    attendu = f"{p}{n}{code3}@loto-quebec.com"
    return Attendu(attendu, transforme=True, trace=[
        f"1re lettre du prénom « {pre} » → {p}",
        f"Nom sans accents → {n}",
        f"3 derniers chiffres du matricule {mat} → {code3}",
        f"Attendu : {attendu}",
    ])


def _concat(largeur: int, col_code: str, col_lib: str) -> Callable[[Champ, Contexte], Attendu]:
    def calc(ch: Champ, ctx: Contexte) -> Attendu:
        code, lib = ctx.src.get(col_code), ctx.src.get(col_lib)
        if est_vide(code) or est_vide(lib):
            return Attendu(None, calculable=False, trace=[f"{col_code} ou {col_lib} vide"])
        n = vers_nombre(code)
        code_fmt = f"{int(n):0{largeur}d}" if n is not None else str(code)
        att = f"{code_fmt}-{lib}"
        return Attendu(att, transforme=True, trace=[
            f"{col_code} = {code} → formaté sur {largeur} positions : {code_fmt}",
            f"{col_lib} = {lib}", f"Concaténation « code-libellé » → {att}"],
            hypotheses=[f"H3 : le code est formaté sur {largeur} positions (format du référentiel cible)"])
    return calc


# --- Situation d'emploi ------------------------------------------------------
ACTIF_CODES = {0, 1}
ABSENCE_CODES = {2, 3, 6, 7}


def _code_acces(ctx: Contexte) -> tuple[int | None, list[str]]:
    """Code de traitement des accès : CodeSuspensionAccès (source), recoupé avec
    CodeGestionAccès de la table des motifs lorsque la jointure existe."""
    trace = []
    v = vers_nombre(ctx.src.get("CodeSuspensionAccès"))
    code = int(v) if v is not None else None
    trace.append(f"CodeSuspensionAccès (source) = {afficher(v)}")
    if ctx.motif is not None:
        m = vers_nombre(ctx.motif.get("CodeGestionAccès"))
        trace.append(f"Jointure motif {ctx.src.get('CodeRaisonStatut')} → CodeGestionAccès = {afficher(m)}"
                     + (" (cohérent)" if m == v else " (INCOHÉRENT avec la source)"))
        if code is None and m is not None:
            code = int(m)
    else:
        trace.append(f"CodeRaisonStatut {ctx.src.get('CodeRaisonStatut')} absent de la table des motifs "
                     "→ on se fie au CodeSuspensionAccès de la source")
    return code, trace


def _statut(ch: Champ, ctx: Contexte) -> Attendu:
    code, trace = _code_acces(ctx)
    if code in ACTIF_CODES:
        return Attendu("Actif", transforme=True, trace=trace + [f"Code {code:02d} ∈ {{00, 01}} → « Actif »"])
    if code in ABSENCE_CODES:
        return Attendu("Absence complète", transforme=True,
                       trace=trace + [f"Code {code:02d} ∈ {{02, 03, 06, 07}} → « Absence complète »"])
    return Attendu(None, calculable=False, trace=trace + [f"Code {code} non couvert par la table de situation"])


def _cad(ch: Champ, ctx: Contexte) -> Attendu:
    code, trace = _code_acces(ctx)
    if code in ACTIF_CODES:
        return Attendu(None, transforme=True, trace=trace + ["Employé actif → aucun code d'absence (cf_CAD vide)"])
    if code in ABSENCE_CODES:
        if ctx.motif is None:
            return Attendu(None, calculable=False,
                           trace=trace + ["Absence mais motif introuvable : code Remphor non calculable"])
        rem = vers_nombre(ctx.motif.get("CodeStatutSystèmeExterne"))
        return Attendu(rem, transforme=True, trace=trace + [
            f"Absence → code Remphor associé au motif {ctx.src.get('CodeRaisonStatut')} = {afficher(rem)}"])
    return Attendu(None, calculable=False, trace=trace + [f"Code {code} non couvert"])


def _cadp(ch: Champ, ctx: Contexte) -> Attendu:
    code, trace = _code_acces(ctx)
    d = vers_date(ctx.src.get("DateRetourAnticipée"))
    if code in ABSENCE_CODES:
        return Attendu(d, transforme=False, calculable=True,
                       trace=trace + [f"Absence → date de retour prévue = DateRetourAnticipée = {afficher(d)}"])
    if code in ACTIF_CODES:
        return Attendu(None, transforme=d is not None,
                       trace=trace + ["Employé actif → pas de date de retour (cf_CADP vide)"])
    return Attendu(None, calculable=False, trace=trace)


# --- Type d'employé ----------------------------------------------------------
CODES_CONTRAT = {"T": "KELH", "O": "WHX", "M": "CEGQ", "R": "CNZC", "J": "RMQ", "Z": "JAW", "Q": "TRSY"}


def _contrat(ch: Champ, ctx: Contexte) -> Attendu:
    cat = str(ctx.src.get("CatégorieEmploi") or "").strip().upper()
    perm, ft = vers_bool(ctx.src.get("EstPermanent")), vers_bool(ctx.src.get("EstTempsPlein"))
    t = [f"EMPTP_CD (CatégorieEmploi) = {cat}, PERM_IND (EstPermanent) = {int(perm) if perm is not None else '?'}"
         f", FT_IND (EstTempsPlein) = {int(ft) if ft is not None else '?'}"]
    if cat == "V" and perm and ft:
        return Attendu("JWN", transforme=True, trace=t + ['SI PERM_IND=1 et FT_IND=1 et EMPTP_CD="V" → JWN'])
    if cat == "V" and perm and ft is False:
        return Attendu("XFLR", transforme=True, trace=t + ['SI PERM_IND=1 et FT_IND=0 et EMPTP_CD="V" → XFLR'])
    if cat in CODES_CONTRAT:
        return Attendu(CODES_CONTRAT[cat], transforme=True,
                       trace=t + [f'SI EMPTP_CD="{cat}" → {CODES_CONTRAT[cat]}'])
    return Attendu(None, calculable=False, trace=t + ["Combinaison non couverte par la règle"])


# --- Type d'affectation ------------------------------------------------------
def _affect(ch: Champ, ctx: Contexte) -> Attendu:
    ta = str(ctx.src.get("TypeAffectation") or "").strip().upper()
    if ta not in {"P", "A", "S"}:
        return Attendu(None, calculable=False, trace=[f"TypeAffectation inconnu : {ta}"])
    prim, temp = ta == "P", ta == "A"
    val = prim if ch.cible == "isPrimaryAssignment" else temp
    lib = {"P": "primaire", "A": "temporaire", "S": "secondaire"}[ta]
    return Attendu(val, transforme=True, trace=[
        f"TypeAffectation = {ta} (affectation {lib})",
        f"→ isPrimaryAssignment = {str(prim).lower()}, isTemporaryAssignment = {str(temp).lower()}"])


# --- Dates d'effet / d'expiration -------------------------------------------
def date_unite_courante(ctx: Contexte) -> tuple[date | None, list[str]]:
    """Date à laquelle l'unité administrative courante est devenue applicable au poste."""
    det = ctx.detail
    courant = str(vers_nombre(ctx.src.get("CodeDirection")) or "")
    if det.empty:
        return None, [f"Aucun détail du poste pour le poste {ctx.src.get('CodePoste')}"]
    unites = [str(vers_nombre(u)) for u in det["CodeDirectionAffectée"]]
    dates = list(det["DateEffet"])
    hist = " → ".join(f"{d.isoformat()}:{int(float(u))}" for d, u in zip(dates, unites))
    trace = [f"Historique du poste {ctx.src.get('CodePoste')} (date:unité) : {hist}"]
    changement = None
    for i in range(1, len(unites)):
        if unites[i] != unites[i - 1] and unites[i] == courant:
            changement = dates[i]
    if changement:
        trace.append(f"Changement vers l'unité courante détecté le {changement.isoformat()}")
        return changement, trace
    if courant not in unites:
        trace.append("Unité courante absente de l'historique → MIN EFFDT par défaut")
    else:
        trace.append("Aucun changement d'unité administrative → MIN EFFDT")
    return min(dates), trace + [f"MIN EFFDT = {min(dates).isoformat()}"]


def _date_debut(ch: Champ, ctx: Contexte) -> Attendu:
    entree = vers_date(ctx.src.get("DateEntréePoste"))
    d_unite, trace = date_unite_courante(ctx)
    trace = [f"DateEntréePoste = {afficher(entree)}"] + trace
    if entree is None and d_unite is None:
        return Attendu(None, calculable=False, trace=trace)
    candidats = [d for d in (entree, d_unite) if d is not None]
    if ctx.params.mode_date_debut == "plus_ancienne":
        att, lib = min(candidats), "la plus ancienne (lecture littérale)"
        hyp = "H1 : lecture littérale « date la plus ancienne »"
    else:
        att, lib = max(candidats), "la plus récente"
        hyp = ("H1 : l'affectation ne peut débuter avant que l'unité administrative courante soit "
               "applicable → on retient la date la plus récente (validé sur les données, voir README)")
    trace.append(f"Date retenue = {lib} entre {afficher(entree)} et {afficher(d_unite)} → {afficher(att)}")
    return Attendu(att, transforme=att != entree, trace=trace, hypotheses=[hyp])


def _date_fin(ch: Champ, ctx: Contexte) -> Attendu:
    sortie = vers_date(ctx.src.get("DateSortiePoste"))
    trace = [f"DateSortiePoste = {afficher(sortie)}"]
    det = ctx.detail
    fin_unite = None
    if not det.empty:
        ref = ctx.params.date_reference
        passes = det[det["DateEffet"] <= ref]
        futurs = det[det["DateEffet"] > ref]
        if not passes.empty and not futurs.empty:
            cour, proch = passes.iloc[-1], futurs.iloc[0]
            if str(vers_nombre(proch["CodeDirectionAffectée"])) != str(vers_nombre(cour["CodeDirectionAffectée"])):
                fin_unite = proch["DateEffet"] - timedelta(days=1)
                trace.append(f"Prochain détail ({proch['DateEffet']}) change d'unité → fin = {fin_unite}")
            else:
                trace.append("Prochain détail du poste : même unité administrative → pas de date de fin")
        else:
            trace.append("Aucun détail du poste postérieur → pas de date de fin d'unité (NULL)")
    candidats = [d for d in (sortie, fin_unite) if d is not None]
    att = min(candidats) if candidats else None
    trace.append(f"Date retenue = la plus ancienne non nulle → {afficher(att)}")
    return Attendu(att, transforme=att != sortie, trace=trace)


REGLES: dict[str, Regle] = {r.id: r for r in (
    Regle("R-DIRECT", "Copie directe", "La valeur cible doit être identique à la valeur source (après normalisation).",
          _direct),
    Regle("R-EMAIL", "Construction du courriel",
          "1re lettre du prénom + nom + 3 derniers chiffres du code + « @loto-quebec.com », sans accents.", _email),
    Regle("R-CONCAT-DIV", "Libellé du département", "Concaténation Unité adm. + « - » + Unité adm. desc.",
          _concat(5, "CodeDirection", "LibelléDirection")),
    Regle("R-CONCAT-POS", "Libellé du rôle", "Concaténation Emploi + « - » + Emploi desc.",
          _concat(4, "CodeEmploi", "IntituléEmploi")),
    Regle("R-SIT-STATUT", "Situation d'emploi",
          "Code de traitement des accès 00/01 → Actif ; 02/03/06/07 → Absence complète.", _statut),
    Regle("R-SIT-CAD", "Code d'absence Remphor (cf_CAD)",
          "Pour une absence, code Remphor associé au motif (jointure CodeRaisonStatut) ; vide si actif.", _cad),
    Regle("R-SIT-CADP", "Date de retour prévue (cf_CADP)",
          "Pour une absence, DateRetourAnticipée ; vide si actif.", _cadp),
    Regle("R-CONTRAT", "Type d'employé", "Table EMPTP_CD / PERM_IND / FT_IND → code de contrat cible.", _contrat),
    Regle("R-AFFECT", "Type d'affectation", "P → primaire ; A → temporaire ; S → les deux à false.", _affect),
    Regle("R-DATE-DEBUT", "Date d'effet du poste",
          "Combinaison de la date d'entrée au poste et de la date où l'unité administrative courante est "
          "devenue applicable (historique du détail du poste).", _date_debut),
    Regle("R-DATE-FIN", "Date d'expiration du poste",
          "Plus ancienne entre DateSortiePoste et la fin de l'unité administrative courante.", _date_fin),
)}

REGLES_ENREGISTREMENT = {
    "R-COMPLETUDE": "Chaque affectation du système A doit exister dans le système B (et inversement).",
}
