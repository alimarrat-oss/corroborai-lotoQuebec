"""Boucle de rétroaction experte.

Un expert fonctionnel peut corriger un verdict :
  - portée « cas »       : la correction s'applique à ce cas précis (clé stable) ;
  - portée « signature » : la correction devient une RÈGLE APPRISE (R-APP-nnn)
    appliquée automatiquement aux futurs cas ayant la même signature de preuves
    (même champ + mêmes types de signaux).
Les corrections servent aussi d'exemples (few-shot) dans les prompts du LLM.
Stockage : fichier JSON versionnable (retroaction/corrections.json).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .modele import Constat

FICHIER_DEFAUT = Path("retroaction/corrections.json")


def charger(fichier: str | Path = FICHIER_DEFAUT) -> list[dict]:
    p = Path(fichier)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def sauvegarder(corrections: list[dict], fichier: str | Path = FICHIER_DEFAUT) -> None:
    p = Path(fichier)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")


def ajouter(corrections: list[dict], c: Constat, verdict: str, commentaire: str, auteur: str = "expert",
            portee: str = "cas", action: str = "") -> dict:
    corrections[:] = [r for r in corrections
                      if not (r["portee"] == portee and r["cle" if portee == "cas" else "signature"]
                              == (c.cle if portee == "cas" else c.signature))]
    n_app = sum(1 for r in corrections if r.get("portee") == "signature") + 1
    r = {
        "cle": c.cle, "signature": c.signature, "champ": c.champ_cible, "portee": portee,
        "verdict_initial": c.verdict, "origine_initiale": c.origine, "verdict": verdict,
        "commentaire": commentaire, "action": action, "auteur": auteur,
        "horodatage": datetime.now().isoformat(timespec="seconds"),
        "valeurs": {"source": c.valeur_source, "attendue": c.valeur_attendue, "cible": c.valeur_cible},
    }
    if portee == "signature":
        r["id_regle"] = f"R-APP-{n_app:03d}"
        r["texte_regle"] = (f"Pour le champ {c.champ_cible}, lorsque les preuves "
                            f"{c.signature.split('|')[1]} sont réunies → {verdict}.")
    corrections.append(r)
    return r


def trouver(corrections: list[dict], c: Constat, portees: tuple[str, ...] = ("cas", "signature")) -> dict | None:
    if "cas" in portees:
        for r in corrections:
            if r.get("portee") == "cas" and r.get("cle") == c.cle:
                return r
    if "signature" in portees:
        for r in corrections:
            if r.get("portee") == "signature" and r.get("signature") == c.signature:
                return r
    return None


def exemples(corrections: list[dict], champ: str, n: int = 5) -> list[dict]:
    return [{"valeurs": r["valeurs"], "verdict_expert": r["verdict"], "commentaire": r["commentaire"]}
            for r in corrections if r.get("champ") == champ][-n:]


def regles_apprises(corrections: list[dict]) -> list[dict]:
    return [r for r in corrections if r.get("portee") == "signature"]
