"""Arbitre local (hors-ligne) : pondération explicable des preuves.

Utilisé lorsque aucun LLM n'est configuré, et comme « second avis »
indépendant lorsque le LLM est disponible (l'accord entre les deux est
mesuré et un désaccord abaisse la confiance → cas à revoir en priorité).
Chaque décision cite l'heuristique d'arbitrage appliquée (A1…A6).
"""
from __future__ import annotations

from ..config import ANOMALIE, JUSTIFIE
from ..modele import Constat

HEURISTIQUES = {
    "A1": "Artefact systémique : correspondance 1-pour-1 stable sur la quasi-totalité des lignes ET structure "
          "de la règle respectée → écart justifié (transcodage / anonymisation / environnement), à confirmer.",
    "A2": "Source vide comblée par une valeur cohérente (détail du poste, valeur des employés au même quart) "
          "→ écart justifié ; compléter la source.",
    "A3": "Valeur source explicite remplacée par une valeur par défaut / contractuelle de la cible → anomalie.",
    "A4": "Date cible issue d'un détail du poste qui ne change pas l'unité administrative → anomalie "
          "(la règle ne retient que les changements d'unité).",
    "A5": "Permutation entre deux employés → anomalie (erreur d'alignement au chargement).",
    "A6": "Aucune preuve suffisante pour justifier l'écart → anomalie à valider.",
}


def arbitrer(c: Constat) -> dict:
    codes = {s.code for s in c.signaux}
    if "S-PERMUTATION" in codes:
        return _d(ANOMALIE, 0.9, "A5", c)
    if "S-TRANSCODAGE" in codes and ({"S-STRUCTURE-CONFORME", "S-PREFIXE-ENV"} & codes):
        conf = 0.75 + (0.05 if {"S-STRUCTURE-CONFORME", "S-PREFIXE-ENV"} <= codes else 0)
        return _d(JUSTIFIE, conf, "A1", c)
    if "S-DETAIL-SANS-CHANGEMENT-UNITE" in codes:
        return _d(ANOMALIE, 0.85, "A4", c)
    if "S-SOURCE-VIDE" in codes and ({"S-VALEUR-DETAIL-POSTE", "S-DEFAUT-CIBLE"} & codes):
        quart_ok = any(s.code == "S-QUART-COHERENT" and s.nature == "explicatif" for s in c.signaux)
        return _d(JUSTIFIE, 0.7 if quart_ok else 0.6, "A2", c)
    if "S-SOURCE-VIDE" not in codes and ({"S-DEFAUT-CIBLE", "S-VALEUR-DETAIL-POSTE"} & codes):
        conf = 0.85 if any(s.code == "S-VALEUR-SOURCE-EXPLICITE" and s.nature == "aggravant" for s in c.signaux) else 0.75
        return _d(ANOMALIE, conf, "A3", c)
    return _d(ANOMALIE, 0.55, "A6", c)


_ACTIONS = {
    "A1": "Confirmer avec l'équipe d'intégration la table de correspondance / l'origine de l'identifiant "
          "(anonymisation ou environnement) ; si confirmé, l'ajouter comme règle de transcodage.",
    "A2": "Compléter la valeur manquante dans le système A – RH pour supprimer la dépendance à la valeur par défaut.",
    "A3": "Vérifier l'interface : la valeur explicite du système A n'est pas transférée (valeur par défaut appliquée). "
          "Corriger la donnée dans le système B.",
    "A4": "Corriger la date d'effet dans le système B et vérifier la logique de calcul de l'interface "
          "(elle semble retenir le dernier détail du poste au lieu du changement d'unité administrative).",
    "A5": "Vérifier l'alignement des enregistrements lors du chargement (valeurs croisées entre deux employés).",
    "A6": "Investiguer manuellement : comparer les deux systèmes pour cet enregistrement.",
}


TITRES = {
    "A1": "Artefact systémique (transcodage / anonymisation / environnement)",
    "A2": "Source vide comblée par une valeur cohérente",
    "A3": "Valeur explicite du système A remplacée par une valeur par défaut",
    "A4": "Date issue d'un détail du poste sans changement d'unité administrative",
    "A5": "Permutation entre deux employés",
    "A6": "Écart inexpliqué",
}


def _d(verdict: str, conf: float, h: str, c: Constat) -> dict:
    preuves = " ".join(f"[{s.code}] {s.description}" for s in c.signaux if s.nature != "contexte")
    return {
        "verdict": verdict,
        "confiance": round(conf, 2),
        "heuristique": h,
        "justification": f"{h} – {TITRES[h]} → {verdict}. {preuves}",
        "action": _ACTIONS[h],
    }
