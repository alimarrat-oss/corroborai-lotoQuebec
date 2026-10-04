"""Analyse de motifs (preuves) sur les écarts non résolus par les règles.

Ces signaux sont calculés de façon déterministe sur l'ENSEMBLE du jeu de
données (et pas ligne par ligne) : ils permettent de reconnaître ce qu'une
règle statique ne voit pas — un transcodage systémique, une permutation entre
deux employés, une valeur par défaut imposée par la cible, une valeur provenant
de l'historique du poste, etc. Ils constituent le dossier de preuves transmis
à l'arbitre IA (LLM ou local).
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

import pandas as pd

from .config import Parametres, champ
from .modele import AGGRAVANT, CONTEXTE, EXPLICATIF, Constat, Signal
from .normalisation import afficher, vers_date, vers_nombre

# paires (code, libellé) dont la cohérence interne peut être vérifiée
PAIRES_CODE_LIBELLE = {
    "siteName": ("siteCode", "CodeSite", "LibelléSite"),
    "divisionName": ("divisionId", "CodeDirection", "LibelléDirection"),
    "positionName": ("positionId", "CodeEmploi", "IntituléEmploi"),
}
HEURES_DETAIL = {"weeklyHoursOverride": "HeuresSemaineContrat", "dailyHoursOverride": "HeuresJourContrat"}


def _forme(s: str) -> str:
    return re.sub(r"\d+", "#", s)


def _groupes(s: str) -> list[int]:
    """Motif d'égalité des groupes de chiffres (ex. '6585-Empl6585' → [0, 0])."""
    g = re.findall(r"\d+", s)
    vus: dict[str, int] = {}
    return [vus.setdefault(x, len(vus)) for x in g]


def _prefixe_commun(valeurs: list[str]) -> str:
    if not valeurs:
        return ""
    p = os.path.commonprefix(valeurs)
    m = re.match(r"^[A-Za-z0-9\-]+_", p)
    return m.group(0) if m else ""


def _chiffres(s) -> set[int]:
    return {int(x) for x in re.findall(r"\d+", str(s))}


def analyser(constats: list[Constat], non_resolus: list[Constat], cible: pd.DataFrame,
             source: pd.DataFrame, params: Parametres) -> None:
    """Ajoute les signaux pertinents à chaque constat non résolu (modifie en place)."""
    par_champ_tous: dict[str, list[Constat]] = defaultdict(list)
    for c in constats:
        par_champ_tous[c.champ_cible].append(c)
    par_champ: dict[str, list[Constat]] = defaultdict(list)
    for c in non_resolus:
        par_champ[c.champ_cible].append(c)

    for nom, ecarts in par_champ.items():
        ch = champ(nom)
        total = len(par_champ_tous[nom])
        cibles_txt = [str(c._cible) for c in ecarts if c._cible is not None]

        # --- Préfixe d'environnement commun à toutes les valeurs cible -----------
        prefixe = ""
        if ch.type == "texte":
            toutes = [str(v) for v in cible[nom].dropna()]
            p = _prefixe_commun(toutes)
            if p and len(toutes) == len(cible) and not any(str(c._attendu or "").startswith(p) for c in ecarts):
                prefixe = p

        def sans_prefixe(v):
            s = str(v) if v is not None else ""
            return s[len(prefixe):] if prefixe and s.startswith(prefixe) else s

        # --- Transcodage systémique (bijection attendu → cible sur la majorité) ---
        couples = [(str(c._attendu), sans_prefixe(c._cible)) for c in ecarts if c._calculable]
        dir_, inv = defaultdict(set), defaultdict(set)
        for a, b in couples:
            dir_[a].add(b); inv[b].add(a)
        bijectif = couples and all(len(v) == 1 for v in dir_.values()) and all(len(v) == 1 for v in inv.values())
        couverture = len(ecarts) / total if total else 0
        systemique = bool(bijectif and couverture >= params.seuil_transcodage and len(dir_) >= 2)

        # --- Valeur par défaut imposée par la cible ------------------------------
        val_cible = Counter(afficher(vers_nombre(v) if ch.type in ("nombre", "code") else v)
                            for v in cible[nom].dropna())
        mode_cible, n_mode = (val_cible.most_common(1)[0] if val_cible else (None, 0))
        part_mode = n_mode / len(cible) if len(cible) else 0
        src_col = ch.sources[0]
        var_source = source[src_col].dropna().nunique() if src_col in source else 0

        # --- Permutations entre employés ------------------------------------------
        index = defaultdict(list)
        for c in ecarts:
            index[(afficher(c._attendu), afficher(c._cible))].append(c)

        for c in ecarts:
            sig: list[Signal] = []
            att, cib = afficher(c._attendu), afficher(c._cible)
            if not c._calculable:
                sig.append(Signal("S-SOURCE-VIDE", EXPLICATIF,
                                  f"La valeur attendue ne peut être calculée (source vide : {c.valeur_source})."))
            if prefixe:
                sig.append(Signal("S-PREFIXE-ENV", EXPLICATIF,
                                  f"Toutes les valeurs cible ({len(cible)}/{len(cible)}) commencent par le préfixe "
                                  f"d'environnement « {prefixe} » absent de la règle."))
            if c._calculable and c._attendu is not None and c._cible is not None and ch.type == "texte" \
                    and ch.regle != "R-DIRECT":
                a, b = str(c._attendu), sans_prefixe(c._cible)
                if _forme(a).casefold() == _forme(b).casefold() and _groupes(a) == _groupes(b):
                    sig.append(Signal("S-STRUCTURE-CONFORME", EXPLICATIF,
                                      f"La valeur cible respecte la structure de la règle (« {_forme(b)} ») ; "
                                      "seuls les identifiants numériques diffèrent."))
            if systemique:
                sig.append(Signal("S-TRANSCODAGE", EXPLICATIF,
                                  f"Écart systémique : {len(ecarts)}/{total} lignes diffèrent selon une "
                                  f"correspondance 1-pour-1 stable ({len(dir_)} valeurs distinctes, aucune "
                                  "contradiction) → transcodage ou anonymisation plutôt qu'erreur ponctuelle."))
            if ch.type in ("nombre", "code") and mode_cible is not None and part_mode >= 0.95 and var_source >= 2 \
                    and cib == mode_cible:
                sig.append(Signal("S-DEFAUT-CIBLE", EXPLICATIF,
                                  f"La cible contient « {mode_cible} » sur {n_mode}/{len(cible)} lignes alors que la "
                                  f"source présente {var_source} valeurs distinctes → valeur par défaut probable."))
            # valeur trouvée dans une source complémentaire (détail du poste)
            det = c._ctx.detail if c._ctx is not None else pd.DataFrame()
            if nom in HEURES_DETAIL and not det.empty:
                vals = {afficher(vers_nombre(v)) for v in det[HEURES_DETAIL[nom]]}
                if cib in vals:
                    sig.append(Signal("S-VALEUR-DETAIL-POSTE", EXPLICATIF,
                                      f"La valeur cible {cib} correspond aux heures contractuelles du poste "
                                      f"{c._ctx.src['CodePoste']} ({HEURES_DETAIL[nom]} = {', '.join(sorted(vals))})."))
                if c._calculable:
                    quart = c._ctx.src.get("CodeQuart")
                    quart_maj = Counter(source["CodeQuart"].dropna()).most_common(1)[0][0]
                    sig.append(Signal("S-VALEUR-SOURCE-EXPLICITE", CONTEXTE if quart == quart_maj else AGGRAVANT,
                                      f"La source porte une valeur explicite {att} avec le code de quart {quart}"
                                      + ("" if quart == quart_maj else f" (≠ quart majoritaire {quart_maj} : horaire "
                                                                        "spécifique de l'employé)") + "."))
                elif c._ctx is not None:
                    quart = c._ctx.src.get("CodeQuart")
                    meme = source[(source["CodeQuart"] == quart) & source[ch.sources[0]].notna()][ch.sources[0]]
                    if not meme.empty:
                        vals_q = Counter(afficher(vers_nombre(v)) for v in meme)
                        v_maj, n_maj = vals_q.most_common(1)[0]
                        sig.append(Signal("S-QUART-COHERENT", EXPLICATIF if v_maj == cib else AGGRAVANT,
                                          f"Les employés au code de quart {quart} ont {ch.sources[0]} = {v_maj} "
                                          f"dans {n_maj}/{len(meme)} cas (valeur cible : {cib})."))
            if ch.type == "date" and not det.empty and c._cible is not None:
                hits = det.index[det["DateEffet"] == c._cible].tolist()
                if hits:
                    pos = det.index.get_loc(hits[0])
                    ligne = det.iloc[pos]
                    prec = det.iloc[pos - 1] if pos > 0 else None
                    chg_unite = prec is not None and vers_nombre(prec["CodeDirectionAffectée"]) != \
                        vers_nombre(ligne["CodeDirectionAffectée"])
                    diffs = []
                    if prec is not None:
                        for col, lib in (("CodeDirectionAffectée", "unité adm."), ("MatriculeGestionnaire", "gestionnaire"),
                                         ("CodePosteSecondaire", "poste secondaire"), ("IndicateurGestion", "ind. gestion"),
                                         ("CodeBudget", "budget")):
                            if str(prec[col]) != str(ligne[col]):
                                diffs.append(f"{lib} {prec[col]}→{ligne[col]}")
                    rang = f"{pos + 1}/{len(det)}"
                    sig.append(Signal("S-VALEUR-DETAIL-POSTE", EXPLICATIF,
                                      f"La date cible {cib} est la date d'effet du détail du poste n° {rang} "
                                      f"(poste {c._ctx.src['CodePoste']})."))
                    sig.append(Signal("S-DETAIL-SANS-CHANGEMENT-UNITE" if not chg_unite else "S-DETAIL-CHANGEMENT-UNITE",
                                      AGGRAVANT if not chg_unite else EXPLICATIF,
                                      ("Ce détail ne modifie PAS l'unité administrative "
                                       f"({afficher(vers_nombre(ligne['CodeDirectionAffectée']))} inchangée)"
                                       if not chg_unite else "Ce détail correspond à un changement d'unité administrative")
                                      + (f" ; il modifie : {', '.join(diffs)}." if diffs else ".")))
            # permutation
            for autre in index.get((cib, att), []):
                if autre is not c and autre.matricule != c.matricule:
                    sig.append(Signal("S-PERMUTATION", AGGRAVANT,
                                      f"Permutation probable avec l'employé {autre.matricule} : sa valeur attendue est "
                                      f"« {cib} » et sa valeur cible « {att} »."))
                    break
            # cohérence interne code ↔ libellé
            if nom in PAIRES_CODE_LIBELLE and c._ctx is not None:
                champ_code, col_code, col_lib = PAIRES_CODE_LIBELLE[nom]
                code_src = vers_nombre(c._ctx.src.get(col_code))
                relation_source = code_src is not None and int(code_src) in _chiffres(c._ctx.src.get(col_lib))
                code_cib = vers_nombre(c._cib_row.get(champ_code)) if c._cib_row is not None else None
                if relation_source and code_cib is not None and int(code_cib) not in _chiffres(c._cible):
                    sig.append(Signal("S-INCOHERENCE-INTERNE", AGGRAVANT,
                                      f"Dans la cible, {champ_code} = {afficher(code_cib)} mais {nom} = « {cib} » : "
                                      f"le libellé ne correspond plus au code (relation respectée dans la source)."))
            c.signaux.extend(sig)
