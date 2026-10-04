"""Moteur de corroboration hybride (règles + IA).

  Niveau 1 – comparaison brute et normalisation   → Conforme
  Niveau 2 – règles métier du mapping              → Conforme / Écart justifié / Anomalie
  Niveau 3 – analyse IA des cas ambigus            → Écart justifié / Anomalie (+ confiance)
  Niveau 4 – rétroaction experte                   → correction / règle apprise
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from . import retroaction, signaux
from .appariement import Paire, apparier
from .chargement import Donnees
from .config import (ANOMALIE, CHAMPS, CONFORME, JUSTIFIE, NIVEAUX, ORIG_DIRECT, ORIG_EXPERT,
                     ORIG_NORMALISATION, ORIG_REGLE, Champ, Parametres)
from .ia import arbitre
from .ia import llm as llm_mod
from .modele import AGGRAVANT, CONTEXTE, EXPLICATIF, Constat, Signal
from .normalisation import (NORMALISATIONS, afficher, brut, egal, normaliser, sans_accents, vers_bool,
                            vers_date)
from .regles import REGLES, Contexte, date_unite_courante

PRIORITE_SYSTEMIQUE = {"S-PERMUTATION", "S-TYPE-ABSENT-CIBLE", "S-DETAIL-SANS-CHANGEMENT-UNITE"}


@dataclass
class Resultat:
    constats: list[Constat]
    paires: list[Paire]
    observations: list[dict]
    validation_hypotheses: list[dict]
    journal: list[str]
    params: Parametres
    empreintes: dict[str, str]
    fournisseur: str
    horodatage: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def tableau(self) -> pd.DataFrame:
        lignes = []
        for c in self.constats:
            d = c.vers_dict()
            lignes.append({
                "ID": c.id, "Matricule": c.matricule, "Affectation": c.affectation,
                "Ligne source": c.ligne_source, "Ligne cible": c.ligne_cible,
                "Champ cible": c.champ_cible, "Libellé": c.libelle, "Champ(s) source": c.champs_source,
                "Valeur source": c.valeur_source, "Valeur attendue": c.valeur_attendue,
                "Valeur cible": c.valeur_cible, "Verdict": c.verdict, "Confiance": c.confiance,
                "Priorité": c.priorite, "Niveau priorité": niveau_priorite(c),
                "Niveau de décision": NIVEAUX.get(c.origine, ""), "Origine de la décision": c.origine,
                "Règle": c.regle, "Justification": c.justification, "Action recommandée": c.action,
                "Diagnostic IA": c.avis_ia.get("diagnostic", ""),
                "Preuves (signaux)": "\n".join(d["signaux"]),
                "Trace du calcul": "\n".join(c.trace),
                "Normalisations": ", ".join(c.normalisations),
                "Hypothèses": "\n".join(c.hypotheses),
                "Avis arbitre local": _fmt_local(c), "Accord LLM/local": _fmt_accord(c),
                "Signature": c.signature if c.ambigu else "", "Clé": c.cle,
            })
        return pd.DataFrame(lignes)

    def stats(self) -> dict[str, Any]:
        v = Counter(c.verdict for c in self.constats)
        return {
            "total": len(self.constats), **{k: v.get(k, 0) for k in (CONFORME, JUSTIFIE, ANOMALIE)},
            "ambigus_ia": sum(c.ambigu for c in self.constats),
            "origines": dict(Counter(c.origine for c in self.constats)),
            "affectations_source": sum(p.i_src is not None for p in self.paires),
            "affectations_cible": sum(p.i_cib is not None for p in self.paires),
            "employes_anomalie": len({c.matricule for c in self.constats if c.verdict == ANOMALIE}),
        }


def niveau_priorite(c: Constat) -> str:
    if c.priorite >= 70:
        return "Haute"
    if c.priorite >= 40:
        return "Moyenne"
    if c.priorite > 0:
        return "Basse"
    return ""


def _fmt_local(c: Constat) -> str:
    loc = c.avis_ia.get("local")
    return f"{loc['heuristique']} → {loc['verdict']} ({loc['confiance']:.0%})" if loc else ""


def _fmt_accord(c: Constat) -> str:
    if "accord_local" not in c.avis_ia:
        return ""
    return "Oui" if c.avis_ia["accord_local"] else "NON – à revoir"


# ---------------------------------------------------------------------------
def _valeur_source(ch: Champ, src: pd.Series) -> str:
    if len(ch.sources) == 1:
        return brut(src.get(ch.sources[0]))
    return " ; ".join(f"{s}={brut(src.get(s))}" for s in ch.sources)


def _detail(donnees: Donnees, code_poste) -> pd.DataFrame:
    d = donnees.detail_poste
    return d[d["IdentifiantPoste"] == str(code_poste)].sort_values("DateEffet").reset_index(drop=True)


def _motif(donnees: Donnees, code) -> pd.Series | None:
    m = donnees.motifs[donnees.motifs["CodeCatégorieStatut"] == str(code)]
    return None if m.empty else m.iloc[0]


def _libelle_aff(src: pd.Series | None, cib: pd.Series | None) -> str:
    if src is not None:
        return f"{src['TypeAffectation']} · poste {src['CodePoste']} · emploi {src['CodeEmploi']}"
    t = "P" if vers_bool(cib.get("isPrimaryAssignment")) else ("A" if vers_bool(cib.get("isTemporaryAssignment")) else "S")
    return f"{t} · emploi {cib['positionId']} (cible)"


def _comparer(ch: Champ, ctx: Contexte, cib: pd.Series, base: dict) -> Constat:
    regle = REGLES[ch.regle]
    att = regle.calcul(ch, ctx)
    cib_brut = cib.get(ch.cible)
    cib_norm, n_c = normaliser(cib_brut, ch.type)
    src_brut = ctx.src.get(ch.sources[0])
    _, n_s = normaliser(src_brut, ch.type)
    c = Constat(**base, champ_cible=ch.cible, champs_source=", ".join(ch.sources), libelle=ch.libelle,
                regle=ch.regle, valeur_source=_valeur_source(ch, ctx.src), valeur_attendue=afficher(att.valeur)
                if att.calculable else "(indéterminée)", valeur_cible=brut(cib_brut), trace=att.trace,
                hypotheses=list(att.hypotheses), impact=ch.impact)
    c._attendu, c._cible, c._calculable, c._ctx, c._cib_row = att.valeur, cib_norm, att.calculable, ctx, cib

    if att.calculable and egal(att.valeur, cib_norm, ch.type):
        if ch.derive:
            c.verdict, c.origine = CONFORME, ORIG_REGLE
            c.normalisations = sorted(set(n_c))
            c.justification = f"Valeur dérivée conforme à la règle {ch.regle} ({regle.titre}) : {att.trace[-1]}"
            if c.normalisations:
                c.justification += " Normalisation : " + " ; ".join(NORMALISATIONS[x] for x in c.normalisations) + "."
        elif not att.transforme:
            if brut(src_brut) == brut(cib_brut):
                c.verdict, c.origine = CONFORME, ORIG_DIRECT
                c.justification = "Valeur identique dans les deux systèmes."
            else:
                c.normalisations = sorted(set(n_s + n_c))
                c.verdict, c.origine = CONFORME, ORIG_NORMALISATION
                c.justification = ("Valeurs identiques après normalisation : "
                                   + " ; ".join(NORMALISATIONS[n] for n in c.normalisations) + ".")
            if ch.regle != "R-DIRECT":
                c.justification += f" Règle {ch.regle} vérifiée : {att.trace[-1]}"
        else:
            c.verdict, c.origine = JUSTIFIE, ORIG_REGLE
            c.normalisations = sorted(set(n_c))
            c.justification = f"Écart expliqué par la règle {ch.regle} ({regle.titre}) : " + " ; ".join(att.trace)
        c.confiance = 1.0
        return c
    if att.calculable and ch.type == "texte" and cib_norm is not None and att.valeur is not None and \
            sans_accents(str(att.valeur)).casefold() == sans_accents(str(cib_norm)).casefold():
        c.verdict, c.origine, c.normalisations = CONFORME, ORIG_NORMALISATION, ["N-ACCENTS"]
        c.justification = "Valeurs identiques sans accents (" + NORMALISATIONS["N-ACCENTS"] + ")."
        return c
    if not att.calculable and cib_norm is None:
        c.verdict, c.origine = CONFORME, ORIG_DIRECT
        c.justification = "Valeur vide dans les deux systèmes."
        return c
    return c  # non résolu → signaux puis IA ou anomalie


def corroborer(donnees: Donnees, params: Parametres | None = None, corrections: list[dict] | None = None,
               fournisseur: llm_mod.FournisseurLLM | None = None, progression=None) -> Resultat:
    params = params or Parametres()
    corrections = corrections if corrections is not None else []
    journal: list[str] = list(donnees.alertes_chargement)
    src_df, cib_df = donnees.source, donnees.cible

    def etape(msg, p):
        journal.append(msg)
        if progression:
            progression(msg, p)

    etape("Appariement des affectations source ↔ cible", 0.1)
    paires = apparier(src_df, cib_df)
    constats: list[Constat] = []
    n = 0

    def nouvel_id():
        nonlocal n
        n += 1
        return f"C-{n:04d}"

    etape("Niveaux 1 et 2 : normalisation et règles métier", 0.25)
    for p in paires:
        src = src_df.loc[p.i_src] if p.i_src is not None else None
        cib = cib_df.loc[p.i_cib] if p.i_cib is not None else None
        aff = _libelle_aff(src, cib)
        poste = src["CodePoste"] if src is not None else f"emploi{cib['positionId']}"
        if src is None or cib is None:
            constats.append(_constat_enregistrement(p, src, cib, aff, poste, cib_df, src_df, nouvel_id()))
            continue
        ctx = Contexte(src=src, detail=_detail(donnees, src["CodePoste"]),
                       motif=_motif(donnees, src.get("CodeRaisonStatut")), params=params)
        for ch in CHAMPS:
            base = dict(id=nouvel_id(), matricule=p.matricule, affectation=aff, cle=f"{p.matricule}|{poste}|{ch.cible}",
                        ligne_source=int(src["_ligne_source"]), ligne_cible=int(cib["_ligne_cible"]))
            constats.append(_comparer(ch, ctx, cib, base))

    non_resolus = [c for c in constats if c.verdict is None]
    etape(f"Analyse de motifs sur {len(non_resolus)} écarts non résolus par les règles", 0.45)
    signaux.analyser(constats, non_resolus, cib_df, src_df, params)

    ambigus = []
    for c in non_resolus:
        explicatifs = [s for s in c.signaux if s.nature == EXPLICATIF]
        if explicatifs or not c._calculable:
            c.ambigu = True
            ambigus.append(c)
        else:
            aggr = [s for s in c.signaux if s.nature == AGGRAVANT]
            c.verdict = ANOMALIE
            c.origine = ORIG_DIRECT if c.regle == "R-DIRECT" else ORIG_REGLE
            c.confiance = 0.99 if aggr else 0.95
            regle = REGLES[c.regle]
            c.justification = (f"La valeur cible « {c.valeur_cible} » ne respecte pas la règle {c.regle} "
                               f"({regle.titre}) : valeur attendue « {c.valeur_attendue} » "
                               f"[{c.trace[-1] if c.trace else ''}]. Aucune preuve n'explique l'écart."
                               + (" Preuves aggravantes : " + " ".join(s.description for s in aggr) if aggr else ""))

    etape(f"Niveau 3 : arbitrage IA de {len(ambigus)} cas ambigus", 0.6)
    arbitre.arbitrer(ambigus, params, donnees.mapping_cellules, corrections,
                     fournisseur if params.ia_active else None, journal)

    etape("Rétroaction experte sur les verdicts déterministes", 0.8)
    for c in constats:
        if not c.ambigu:
            r = retroaction.trouver(corrections, c, portees=("cas",))
            if r is not None and r["verdict"] != c.verdict:
                c.avis_ia["verdict_avant_expert"] = f"{c.verdict} ({c.origine})"
                c.verdict, c.origine, c.confiance = r["verdict"], ORIG_EXPERT, 1.0
                c.justification = f"Décision experte ({r.get('auteur')}) : {r.get('commentaire')} — " \
                                  f"verdict initial : {c.avis_ia['verdict_avant_expert']}."

    anomalies_det = [c for c in constats if c.verdict == ANOMALIE and not c.ambigu and c.origine != ORIG_EXPERT]
    etape(f"Diagnostic IA de {len(anomalies_det)} anomalies déterministes", 0.85)
    arbitre.enrichir(anomalies_det, fournisseur if params.ia_active else None, params, journal)
    for c in anomalies_det:
        if not c.action:
            c.action = "Corriger la valeur dans le système B – Temps selon la valeur attendue, puis vérifier l'interface."

    for c in constats:
        c.priorite = _priorite(c)

    etape("Observations de qualité et validation des hypothèses", 0.95)
    obs = _observations(donnees, params, constats)
    valid = _valider_hypotheses(donnees, paires, params)
    nom_f = f"{fournisseur.nom}/{fournisseur.modele}" if (fournisseur and params.ia_active) else "arbitre local (hors-ligne)"
    etape("Corroboration terminée", 1.0)
    return Resultat(constats, paires, obs, valid, journal, params, donnees.empreintes, nom_f)


def _constat_enregistrement(p: Paire, src, cib, aff, poste, cib_df, src_df, id_) -> Constat:
    sig = []
    if src is not None:
        t = str(src["TypeAffectation"]).upper()
        col = {"A": "isTemporaryAssignment", "P": "isPrimaryAssignment"}.get(t)
        if col:
            nb = sum(bool(vers_bool(v)) for v in cib_df[col])
            if nb == 0:
                nb_src = int((src_df["TypeAffectation"].str.upper() == t).sum())
                sig.append(Signal("S-TYPE-ABSENT-CIBLE", AGGRAVANT,
                                  f"La cible ne contient AUCUNE affectation de type {t} ({col} = true sur 0/{len(cib_df)} "
                                  f"lignes) alors que la source en compte {nb_src} : ce type d'affectation ne semble "
                                  "pas transmis par l'interface (problème systémique)."))
        autres = cib_df[cib_df["personId"] == p.matricule]
        if not autres.empty:
            sig.append(Signal("S-EMPLOYE-PRESENT", CONTEXTE,
                              f"L'employé existe dans la cible avec {len(autres)} autre(s) affectation(s) : seule "
                              "cette affectation manque."))
        vs = (f"Type {src['TypeAffectation']} · poste {src['CodePoste']} · emploi {src['CodeEmploi']} · "
              f"début {afficher(vers_date(src['DateEntréePoste']))} · unité {src['CodeDirection']}")
        just = (f"L'affectation source (ligne {src['_ligne_source']}) n'a aucune correspondance dans le système B "
                f"({p.details}). Règle R-COMPLETUDE : chaque affectation du système A doit être transférée.")
        vc = "(absente)"
    else:
        vs = "(absente)"
        vc = (f"emploi {cib['positionId']} · début {afficher(vers_date(cib['assignmentStartDate']))} · "
              f"unité {cib['divisionId']}")
        just = (f"L'affectation cible (ligne {cib['_ligne_cible']}) n'a aucune origine dans le système A ({p.details}). "
                "Règle R-COMPLETUDE.")
    c = Constat(id=id_, matricule=p.matricule, affectation=aff, cle=f"{p.matricule}|{poste}|(affectation)",
                ligne_source=int(src["_ligne_source"]) if src is not None else None,
                ligne_cible=int(cib["_ligne_cible"]) if cib is not None else None,
                champ_cible="(affectation entière)", champs_source="(enregistrement)", libelle="Complétude",
                regle="R-COMPLETUDE", valeur_source=vs, valeur_attendue="Affectation présente", valeur_cible=vc,
                verdict=ANOMALIE, origine=ORIG_REGLE, confiance=0.97, justification=just, signaux=sig, impact=1.0,
                trace=["Appariement : " + p.details],
                action="Créer l'affectation manquante dans le système B et vérifier pourquoi l'interface ne transmet "
                       "pas ce type d'affectation.")
    return c


def _priorite(c: Constat) -> float:
    if c.verdict == ANOMALIE:
        s = 100 * c.impact * c.confiance
        if any(x.code in PRIORITE_SYSTEMIQUE for x in c.signaux):
            s += 10
        if c.avis_ia.get("accord_local") is False:
            s += 5
        return round(min(s, 100), 1)
    if c.verdict == JUSTIFIE and c.confiance < 0.8:   # écart justifié « à confirmer »
        return round(100 * c.impact * (1 - c.confiance) * 0.8, 1)
    return 0.0


def _observations(donnees: Donnees, params: Parametres, constats: list[Constat]) -> list[dict]:
    obs = []
    src = donnees.source
    codes_motif = set(donnees.motifs["CodeCatégorieStatut"])
    inconnus = src[~src["CodeRaisonStatut"].isin(codes_motif)]
    for code, g in inconnus.groupby("CodeRaisonStatut"):
        obs.append({"Code": "O-MOTIF-INCONNU", "Gravité": "Faible", "Objet": f"CodeRaisonStatut {code}",
                    "Description": f"{len(g)} ligne(s) source ({g['Matricule'].nunique()} employé(s)) ont un motif "
                                   "absent de la table « Motif de la situation d'emploi ». Sans impact ici (employés "
                                   "actifs : le code d'absence n'est pas requis), mais la jointure échouerait pour "
                                   "une absence. Compléter la table de référence."})
    for _, r in src.iterrows():
        d = vers_date(r["DateRetourAnticipée"])
        if d and d < params.date_reference and str(r["CodeSuspensionAccès"]).strip() in {"2", "3", "6", "7"}:
            ans = (params.date_reference - d).days / 365.25
            obs.append({"Code": "O-RETOUR-DEPASSE", "Gravité": "Moyenne", "Objet": f"Matricule {r['Matricule']}",
                        "Description": f"Absence complète avec date de retour prévue le {d} (dépassée de {ans:.1f} ans). "
                                       "Les deux systèmes concordent, mais la situation est probablement périmée "
                                       "dans le système A – à revalider auprès des RH."})
    for _, r in src.iterrows():
        for col in ("HeuresNormeHebdo", "HeuresNormeQuotidienne"):
            if pd.isna(r[col]):
                obs.append({"Code": "O-SOURCE-VIDE", "Gravité": "Moyenne", "Objet": f"Matricule {r['Matricule']}",
                            "Description": f"{col} est vide dans le système A (poste {r['CodePoste']}) : la valeur "
                                           "cible ne peut pas être corroborée directement."})
    det = donnees.detail_poste
    anterieurs = []
    for _, r in src.iterrows():
        g = det[det["IdentifiantPoste"] == r["CodePoste"]]
        emb = vers_date(r["DateEmbaucheRécente"])
        if not g.empty and emb and g["DateEffet"].max() < emb:
            anterieurs.append(f"{r['CodePoste']}")
    if anterieurs:
        obs.append({"Code": "O-HISTORIQUE-ANTERIEUR", "Gravité": "Info", "Objet": f"{len(anterieurs)} postes",
                    "Description": "L'historique « détail du poste » de ces postes est entièrement antérieur à "
                                   "l'embauche de l'employé (dates probablement décalées par l'anonymisation) : "
                                   f"{', '.join(anterieurs)}. Sans effet sur R-DATE-DEBUT (la date d'entrée au poste "
                                   "est plus récente)."})
    return obs


def _valider_hypotheses(donnees: Donnees, paires: list[Paire], params: Parametres) -> list[dict]:
    """H1 : quelle lecture de la règle « date d'effet du poste » est cohérente avec les données ?"""
    res = []
    for mode, lib in (("plus_recente", "max(date unité adm., date entrée poste)"),
                      ("plus_ancienne", "min(date unité adm., date entrée poste) – lecture littérale")):
        p2 = Parametres(**{**params.__dict__, "mode_date_debut": mode})
        ok = tot = 0
        for p in paires:
            if p.i_src is None or p.i_cib is None:
                continue
            src, cib = donnees.source.loc[p.i_src], donnees.cible.loc[p.i_cib]
            ctx = Contexte(src=src, detail=_detail(donnees, src["CodePoste"]), motif=None, params=p2)
            att = REGLES["R-DATE-DEBUT"].calcul(None, ctx)
            tot += 1
            ok += att.valeur == vers_date(cib["assignmentStartDate"])
        res.append({"Hypothèse": "H1 – R-DATE-DEBUT", "Interprétation": lib, "Concordances": f"{ok}/{tot}",
                    "Retenue": mode == params.mode_date_debut})
    return res
