"""Tests de non-régression : règles, appariement, IA (LLM simulé), rétroaction, rapport, lecture seule."""
from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import openpyxl
import pandas as pd
import pytest

from corroborai import rapport, retroaction
from corroborai.chargement import charger, trouver_fichier, verifier_integrite
from corroborai.config import ANOMALIE, CONFORME, JUSTIFIE, ORIG_APPRIS, ORIG_IA_LLM, ORIG_IA_LOCAL, Parametres, champ
from corroborai.ia import llm as llm_mod
from corroborai.ia.arbitre import pseudonymiser
from corroborai.moteur import corroborer
from corroborai.regles import REGLES, Contexte

DATA = Path(__file__).resolve().parents[1] / "data"
PARAMS = Parametres(date_reference=date(2026, 10, 3))

# Vérité attendue : (matricule, champ) des anomalies réelles
ANOMALIES_ATTENDUES = {
    ("1545850", "(affectation entière)"),
    ("9989151", "assignmentStartDate"), ("3241002", "assignmentStartDate"), ("4402456", "assignmentStartDate"),
    ("2762457", "contractTypeCode"), ("4625374", "contractTypeCode"),
    ("3712987", "contractTypeCode"), ("7254364", "contractTypeCode"),
    ("6035643", "siteName"), ("3241002", "siteName"),
    ("2911996", "weeklyHoursOverride"), ("2911996", "dailyHoursOverride"),
    ("4402456", "weeklyHoursOverride"), ("4402456", "dailyHoursOverride"),
    ("7683990", "weeklyHoursOverride"), ("7683990", "dailyHoursOverride"),
}


@pytest.fixture(scope="module")
def donnees():
    return charger(dossier=DATA)


@pytest.fixture(scope="module")
def res(donnees):
    return corroborer(donnees, PARAMS, corrections=[], fournisseur=None)


def _c(res, mat, ch):
    return [c for c in res.constats if c.matricule == mat and c.champ_cible == ch]


# --- Résultats globaux -------------------------------------------------------
def test_anomalies_exactes(res):
    trouvees = {(c.matricule, c.champ_cible) for c in res.constats if c.verdict == ANOMALIE}
    assert trouvees == ANOMALIES_ATTENDUES


def test_tous_les_constats_ont_verdict_et_justification(res):
    for c in res.constats:
        assert c.verdict in (CONFORME, JUSTIFIE, ANOMALIE), c.id
        assert c.justification.strip(), c.id
        assert c.origine, c.id


def test_seuls_les_champs_du_mapping_sont_corroborés(res):
    champs = {c.champ_cible for c in res.constats} - {"(affectation entière)"}
    assert "siteId" not in champs and "termStartDate" not in champs and "activityStatus" not in champs
    assert len(champs) == 25


def test_lecture_seule(donnees, res):
    assert all(verifier_integrite(DATA, donnees.empreintes).values())


# --- Règles déterministes ----------------------------------------------------
def test_regle_contrat_justifie(res):
    c = _c(res, "1545850", "contractTypeCode")[0]
    assert c.verdict == JUSTIFIE and c.valeur_attendue == "JWN" and c.regle == "R-CONTRAT"


def test_regle_situation_absence(res):
    c = _c(res, "7603160", "statusReasonCode")[0]
    assert c.verdict == JUSTIFIE and c.valeur_attendue == "170"     # 807 → Remphor 170
    s = _c(res, "7603160", "detailedStatus")[0]
    assert s.verdict == CONFORME and "N-ENCODAGE" in s.normalisations  # « Absence complÃ¨te »


def test_regle_affectation_secondaire(res):
    cs = [c for c in _c(res, "7683990", "isPrimaryAssignment") if c.affectation.startswith("S")]
    assert len(cs) == 2 and all(c.valeur_attendue == "false" and c.verdict == JUSTIFIE for c in cs)


def test_concat_division_padding(res):
    c = _c(res, "1545850", "divisionName")[0]
    assert c.valeur_attendue == "00397-UnitAdmin00397" and c.verdict == JUSTIFIE


def test_normalisation_date(res):
    c = _c(res, "1545850", "onboardDate")[0]
    assert c.verdict == CONFORME and c.normalisations == ["N-DATE"]


def test_date_unite_changement_detecte(donnees):
    src = donnees.source[donnees.source["Matricule"] == "2173396"].iloc[0]
    det = donnees.detail_poste[donnees.detail_poste["IdentifiantPoste"] == "69289"].sort_values("DateEffet")
    att = REGLES["R-DATE-DEBUT"].calcul(champ("assignmentStartDate"),
                                        Contexte(src, det.reset_index(drop=True), None, PARAMS))
    assert any("Changement vers l'unité courante détecté le 1997-06-24" in t for t in att.trace)
    assert att.valeur == date(2019, 4, 29)


def test_hypothese_h1_validee(res):
    v = {h["Interprétation"][:3]: h["Concordances"] for h in res.validation_hypotheses}
    assert v["max"] == "19/22" and v["min"] == "0/22"


# --- Appariement -------------------------------------------------------------
def test_affectation_temporaire_manquante(res):
    c = _c(res, "1545850", "(affectation entière)")[0]
    assert c.verdict == ANOMALIE and any(s.code == "S-TYPE-ABSENT-CIBLE" for s in c.signaux)


def test_multi_affectations_appariees(res):
    p = [x for x in res.paires if x.matricule == "7683990"]
    assert len(p) == 3 and all(x.i_src is not None and x.i_cib is not None for x in p)


# --- IA : signaux et arbitrage local ----------------------------------------
def test_ia_transcodage_systemique(res):
    for ch in ("positionName", "contactEmail"):
        cs = [c for c in res.constats if c.champ_cible == ch]
        assert all(c.verdict == JUSTIFIE and c.origine == ORIG_IA_LOCAL for c in cs)
        assert all(any(s.code == "S-TRANSCODAGE" for s in c.signaux) for c in cs)


def test_ia_heures_par_defaut(res):
    assert _c(res, "2911996", "weeklyHoursOverride")[0].verdict == ANOMALIE
    vide = _c(res, "3712987", "weeklyHoursOverride")[0]
    assert vide.verdict == JUSTIFIE and vide.confiance < 0.8 and vide.priorite > 0  # « à confirmer »


def test_ia_date_detail_sans_changement_unite(res):
    c = _c(res, "9989151", "assignmentStartDate")[0]
    assert c.verdict == ANOMALIE and c.avis_ia["local"]["heuristique"] == "A4"


def test_permutation_detectee(res):
    c = _c(res, "6035643", "siteName")[0]
    assert any(s.code == "S-PERMUTATION" and "3241002" in s.description for s in c.signaux)


# --- LLM simulé --------------------------------------------------------------
class _FauxClient:
    def __init__(self, verdict="Écart justifié", invalide=False):
        self.verdict, self.invalide, self.appels = verdict, invalide, []
        self.messages = self

    def create(self, **kw):
        self.appels.append(kw)
        txt = kw["messages"][0]["content"]
        if "decisions" in kw["tools"][0]["input_schema"]["required"]:
            cas = json.loads(txt.split("\n", 1)[1])["cas"]
            out = {"decisions": [{"id": c["id"], "verdict": "Peut-être" if self.invalide else self.verdict,
                                  "confiance": 0.9, "justification": "j", "action": "a"} for c in cas]}
        else:
            out = {"elements": [{"id": c["id"], "diagnostic": "d", "action": "a"} for c in json.loads(txt)["cas"]]}
        return NS(content=[NS(type="tool_use", input=out)])


@pytest.fixture
def cache_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "DOSSIER_CACHE", tmp_path)


def test_llm_desaccord_reduit_confiance(donnees, cache_tmp):
    client = _FauxClient("Écart justifié")
    r = corroborer(donnees, PARAMS, [], llm_mod.Anthropic("faux", client=client))
    c = _c(r, "2911996", "weeklyHoursOverride")[0]          # local : anomalie (A3) ; LLM : justifié
    assert c.origine == ORIG_IA_LLM and c.avis_ia["accord_local"] is False and c.confiance <= 0.5
    e = _c(r, "1545850", "contactEmail")[0]                  # accord
    assert e.avis_ia["accord_local"] is True and e.confiance == 0.9
    # les verdicts déterministes ne sont jamais modifiés par le LLM
    assert _c(r, "2762457", "contractTypeCode")[0].verdict == ANOMALIE


def test_llm_pseudonymisation(donnees, cache_tmp):
    client = _FauxClient()
    corroborer(donnees, PARAMS, [], llm_mod.Anthropic("faux", client=client))
    envoye = json.dumps([a["messages"][0]["content"] for a in client.appels], ensure_ascii=False)
    for m in donnees.source["Matricule"].unique():
        assert m not in envoye


def test_llm_sortie_invalide_repli_local(donnees, cache_tmp):
    r = corroborer(donnees, PARAMS, [], llm_mod.Anthropic("faux", client=_FauxClient(invalide=True)))
    assert all(c.origine == ORIG_IA_LOCAL for c in r.constats if c.ambigu)


def test_pseudonymiser_regle_courriel():
    t = pseudonymiser("matricule 1545850 → 850 ; PNom1545850850@x", {"1545850"})
    assert "1545850" not in t
    alias = t.split("matricule ")[1].split(" ")[0]
    assert f"PNom{alias}{alias[-3:]}@" in t and f"→ {alias[-3:]}" in t   # structure de la règle préservée


# --- Rétroaction -------------------------------------------------------------
def test_retroaction_regle_apprise(donnees, res):
    corr: list[dict] = []
    c = _c(res, "1545850", "positionName")[0]
    r = retroaction.ajouter(corr, c, ANOMALIE, "Table d'emplois non synchronisée", "expert", "signature")
    assert r["id_regle"] == "R-APP-001"
    r2 = corroborer(donnees, PARAMS, corr, None)
    cs = [x for x in r2.constats if x.champ_cible == "positionName"]
    assert all(x.verdict == ANOMALIE and x.origine == ORIG_APPRIS for x in cs)


def test_retroaction_cas_sur_verdict_deterministe(donnees, res):
    corr: list[dict] = []
    c = _c(res, "6035643", "siteName")[0]
    retroaction.ajouter(corr, c, JUSTIFIE, "Déménagement validé", "expert", "cas")
    r2 = corroborer(donnees, PARAMS, corr, None)
    assert _c(r2, "6035643", "siteName")[0].verdict == JUSTIFIE
    assert _c(r2, "3241002", "siteName")[0].verdict == ANOMALIE


# --- Rapport -----------------------------------------------------------------
def test_rapport_excel(res):
    wb = openpyxl.load_workbook(io.BytesIO(rapport.exporter_excel(res)))
    assert {"Synthèse", "Anomalies", "Écarts justifiés", "Analyse IA", "Détail complet",
            "Catalogue des règles"} <= set(wb.sheetnames)
    an = pd.read_excel(io.BytesIO(rapport.exporter_excel(res)), sheet_name="Anomalies")
    assert len(an) == 16 and an["Priorité"].is_monotonic_decreasing


def test_televersement_octets():
    from corroborai.chargement import NOMS_FICHIERS
    d = charger(fichiers={k: trouver_fichier(DATA, k).read_bytes() for k in NOMS_FICHIERS})
    assert len(d.source) == 23 and len(d.cible) == 22
