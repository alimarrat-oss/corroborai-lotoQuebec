"""Niveau 3 – Analyse assistée par l'IA des écarts ambigus.

Pipeline :
  1. Rétroaction experte : une correction sur ce cas (ou une règle apprise sur
     la même signature de preuves) est appliquée en priorité.
  2. Arbitre local (hors-ligne) : décision explicable, toujours calculée.
  3. LLM (si configuré) : les cas sont regroupés par « signature » (champ +
     types de preuves) et soumis par grappe avec leur dossier de preuves.
     La sortie est contrainte par un schéma JSON et validée ; en cas d'échec
     on retombe sur l'arbitre local.
  4. Accord LLM ↔ local mesuré : un désaccord réduit la confiance et marque
     le cas « à revoir ».
  5. Enrichissement : le LLM rédige un diagnostic et une action pour les
     anomalies déterministes (sans pouvoir modifier leur verdict).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from typing import Any

from .. import retroaction
from ..config import ANOMALIE, JUSTIFIE, ORIG_APPRIS, ORIG_EXPERT, ORIG_IA_LLM, ORIG_IA_LOCAL, Parametres
from ..config import champ as get_champ
from ..modele import Constat
from . import llm as llm_mod
from . import local

SYSTEME = """Tu es analyste principal en qualité de données chez Loto-Québec. Tu corrobores les données \
transférées du Système A – RH (maître) vers le Système B – Temps.

On te soumet des écarts que les règles métier déterministes n'ont PAS pu trancher, avec un dossier de preuves \
calculées sur l'ensemble du jeu de données (signaux S-…). Pour chaque cas, rends un verdict :
- « Écart justifié » : la différence s'explique (transformation légitime, artefact technique systémique \
comme un transcodage/anonymisation/préfixe d'environnement, valeur par défaut acceptable comblant une source vide). \
La donnée cible n'a pas à être corrigée.
- « Anomalie » : la donnée cible est erronée ou incomplète au regard du système maître et doit être investiguée.

Exigences :
- Fonde-toi UNIQUEMENT sur les valeurs, la règle officielle et les signaux fournis ; n'invente aucun fait.
- Quand des preuves se contredisent (explicatif vs aggravant), dis laquelle domine et pourquoi.
- Le système A est la source de vérité : une valeur explicite de A remplacée par une valeur par défaut est une anomalie.
- Confiance calibrée entre 0 et 1 (0,9+ seulement si les preuves sont univoques).
- Justification en français, 2 à 3 phrases, compréhensible par une équipe fonctionnelle, citant les codes S-….
- Action recommandée concrète (qui vérifie quoi, où)."""

SCHEMA_VERDICTS = {
    "type": "object",
    "properties": {
        "decisions": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "verdict": {"type": "string", "enum": [JUSTIFIE, ANOMALIE]},
            "confiance": {"type": "number"},
            "justification": {"type": "string"},
            "action": {"type": "string"},
            "signaux_determinants": {"type": "array", "items": {"type": "string"}},
        }, "required": ["id", "verdict", "confiance", "justification", "action"]}},
        "synthese": {"type": "string"},
    },
    "required": ["decisions"],
}

SYSTEME_DIAG = """Tu es analyste qualité de données chez Loto-Québec. Des anomalies ont été CONFIRMÉES par des règles \
déterministes entre le Système A – RH et le Système B – Temps. Tu ne changes pas le verdict. Pour chaque cas, \
rédige en français : (1) un diagnostic probable de la cause (1–2 phrases, en t'appuyant uniquement sur les valeurs \
et signaux fournis, en signalant les motifs communs entre cas), (2) une action de correction concrète."""

SCHEMA_DIAG = {
    "type": "object",
    "properties": {"elements": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "diagnostic": {"type": "string"}, "action": {"type": "string"}},
        "required": ["id", "diagnostic", "action"]}}},
    "required": ["elements"],
}

TAILLE_LOT = 20


# --- Pseudonymisation (minimisation des données envoyées au LLM) -------------
def _alias(mat: str) -> str:
    sel = os.environ.get("CORROBORAI_SEL", "corroborai")
    h = int(hashlib.sha256((sel + str(mat)).encode()).hexdigest(), 16)
    return str(1_000_000 + h % 8_999_999)


def pseudonymiser(texte: str, matricules: set[str]) -> str:
    for m in sorted(matricules, key=len, reverse=True):
        if m:
            a = _alias(m)
            # conserver la cohérence de la règle courriel (3 derniers chiffres du code)
            texte = texte.replace(f"{m}{m[-3:]}@", f"{a}{a[-3:]}@")
            texte = texte.replace(f"matricule {m} → {m[-3:]}", f"matricule {a} → {a[-3:]}")
            texte = re.sub(re.escape(m), a, texte)
    return texte


def _dossier(cas: list[Constat], params: Parametres, exemples: list[dict], donnees_mapping: dict) -> str:
    ch = get_champ(cas[0].champ_cible)
    regle_txt = "\n".join(f"  {k}: {donnees_mapping.get(k, '')}" for k in ch.cellules)
    d = {
        "champ_cible": ch.cible, "libelle": ch.libelle, "champs_source": list(ch.sources),
        "regle_appliquee": cas[0].regle, "texte_officiel_du_mapping": regle_txt,
        "hypotheses": sorted({h for c in cas for h in c.hypotheses}),
        "corrections_expertes_anterieures": exemples,
        "cas": [{
            "id": c.id, "employe": c.matricule, "affectation": c.affectation,
            "valeur_source": c.valeur_source, "valeur_attendue_par_regle": c.valeur_attendue,
            "valeur_cible": c.valeur_cible, "trace_du_calcul": c.trace,
            "signaux": [{"code": s.code, "nature": s.nature, "description": s.description} for s in c.signaux],
        } for c in cas],
    }
    return "Dossier de preuves (JSON) :\n" + json.dumps(d, ensure_ascii=False, indent=1)


def _valider(rep: dict, ids: set[str]) -> dict[str, dict]:
    ok = {}
    for d in rep.get("decisions", []) or []:
        if d.get("id") in ids and d.get("verdict") in (JUSTIFIE, ANOMALIE):
            try:
                d["confiance"] = max(0.0, min(1.0, float(d.get("confiance", 0.5))))
            except (TypeError, ValueError):
                d["confiance"] = 0.5
            if str(d.get("justification", "")).strip():
                ok[d["id"]] = d
    return ok


def arbitrer(ambigus: list[Constat], params: Parametres, mapping: dict[str, str],
             corrections: list[dict], fournisseur: llm_mod.FournisseurLLM | None,
             journal: list[str]) -> None:
    restants = []
    # 1. Rétroaction experte -------------------------------------------------
    for c in ambigus:
        r = retroaction.trouver(corrections, c)
        if r is not None:
            portee = r.get("portee", "cas")
            c.verdict = r["verdict"]
            c.origine = ORIG_EXPERT if portee == "cas" else ORIG_APPRIS
            c.confiance = 1.0 if portee == "cas" else 0.9
            c.justification = (f"Décision experte ({r.get('auteur', 'expert')}, {r.get('horodatage', '')[:10]}) : "
                               f"{r.get('commentaire', '')}" if portee == "cas" else
                               f"Règle apprise {r.get('id_regle', '')} à partir d'une correction experte sur un cas "
                               f"de même signature ({c.signature}) : {r.get('commentaire', '')}")
            c.action = r.get("action", "") or c.action
            c.avis_ia["retroaction"] = r
        else:
            restants.append(c)

    # 2. Arbitre local (toujours calculé) -----------------------------------
    for c in restants:
        c.avis_ia["local"] = local.arbitrer(c)

    # 3. LLM par grappe ------------------------------------------------------
    decisions_llm: dict[str, dict] = {}
    if fournisseur is not None and restants:
        grappes: dict[str, list[Constat]] = defaultdict(list)
        for c in restants:
            grappes[c.signature].append(c)
        for sig, cas in grappes.items():
            exemples = retroaction.exemples(corrections, cas[0].champ_cible)
            for i in range(0, len(cas), TAILLE_LOT):
                lot = cas[i:i + TAILLE_LOT]
                texte = _dossier(lot, params, exemples, mapping)
                if params.pseudonymiser_llm:
                    texte = pseudonymiser(texte, {c.matricule for c in restants} | _matricules_cites(lot))
                try:
                    rep = fournisseur.json(SYSTEME, texte, SCHEMA_VERDICTS)
                    valides = _valider(rep, {c.id for c in lot})
                    decisions_llm.update(valides)
                    journal.append(f"LLM {fournisseur.nom}/{fournisseur.modele} – grappe « {sig} » : "
                                   f"{len(valides)}/{len(lot)} décisions valides")
                    if rep.get("synthese"):
                        for c in lot:
                            c.avis_ia["synthese_grappe"] = rep["synthese"]
                except Exception as e:  # réseau, quota, schéma…
                    journal.append(f"LLM indisponible pour « {sig} » ({type(e).__name__}: {e}) → arbitre local")

    # 4. Décision finale + accord -------------------------------------------
    for c in restants:
        loc = c.avis_ia["local"]
        d = decisions_llm.get(c.id)
        if d is None:
            c.verdict, c.confiance = loc["verdict"], loc["confiance"]
            c.origine = ORIG_IA_LOCAL
            c.justification = loc["justification"]
            c.action = loc["action"]
            c.avis_ia["fournisseur"] = "local"
            continue
        c.avis_ia["llm"] = d
        c.avis_ia["fournisseur"] = f"{fournisseur.nom}/{fournisseur.modele}"
        accord = d["verdict"] == loc["verdict"]
        c.avis_ia["accord_local"] = accord
        c.verdict, c.origine = d["verdict"], ORIG_IA_LLM
        c.confiance = round(d["confiance"] if accord else min(d["confiance"], 0.5), 2)
        c.justification = d["justification"] + ("" if accord else
                                                 f" ⚠ Désaccord avec l'arbitre local ({loc['heuristique']} → "
                                                 f"{loc['verdict']}) : cas à revoir par un expert.")
        c.action = d.get("action", "") or loc["action"]


def _matricules_cites(lot: list[Constat]) -> set[str]:
    out = set()
    for c in lot:
        for s in c.signaux:
            out |= set(re.findall(r"employé (\d{5,})", s.description))
    return out


# --- Enrichissement des anomalies déterministes ------------------------------
def diagnostic_local(c: Constat) -> str:
    codes = {s.code: s for s in c.signaux}
    if "S-PERMUTATION" in codes:
        return ("Valeurs croisées entre deux employés : erreur probable d'alignement des enregistrements "
                "lors du chargement dans le système B. " + codes["S-PERMUTATION"].description)
    if "S-INCOHERENCE-INTERNE" in codes:
        return "Le libellé cible ne correspond plus à son propre code : mise à jour partielle de l'enregistrement."
    if "S-TYPE-ABSENT-CIBLE" in codes:
        return codes["S-TYPE-ABSENT-CIBLE"].description
    return "La valeur cible contredit la règle déterministe ; aucune preuve n'explique l'écart."


def enrichir(anomalies: list[Constat], fournisseur: llm_mod.FournisseurLLM | None, params: Parametres,
             journal: list[str]) -> None:
    for c in anomalies:
        c.avis_ia["diagnostic"] = diagnostic_local(c)
        c.avis_ia.setdefault("fournisseur_diagnostic", "local")
    if fournisseur is None or not anomalies:
        return
    par_champ: dict[str, list[Constat]] = defaultdict(list)
    for c in anomalies:
        par_champ[c.champ_cible].append(c)
    for nom, cas in par_champ.items():
        d = {"champ": nom, "cas": [{"id": c.id, "employe": c.matricule, "affectation": c.affectation,
                                    "regle": c.regle, "valeur_source": c.valeur_source,
                                    "valeur_attendue": c.valeur_attendue, "valeur_cible": c.valeur_cible,
                                    "justification_deterministe": c.justification,
                                    "signaux": [s.court() for s in c.signaux]} for c in cas]}
        texte = json.dumps(d, ensure_ascii=False, indent=1)
        if params.pseudonymiser_llm:
            texte = pseudonymiser(texte, {c.matricule for c in anomalies} | _matricules_cites(cas))
        try:
            rep = fournisseur.json(SYSTEME_DIAG, texte, SCHEMA_DIAG)
            for e in rep.get("elements", []):
                for c in cas:
                    if c.id == e.get("id") and e.get("diagnostic"):
                        c.avis_ia["diagnostic"] = e["diagnostic"]
                        c.avis_ia["fournisseur_diagnostic"] = f"{fournisseur.nom}/{fournisseur.modele}"
                        if e.get("action"):
                            c.action = e["action"]
        except Exception as e:
            journal.append(f"Diagnostic LLM indisponible pour {nom} ({type(e).__name__}) → diagnostic local")
