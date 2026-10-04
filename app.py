"""CorroborIA – démonstrateur Streamlit.

    streamlit run app.py
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from corroborai import __version__, rapport, retroaction
from corroborai.chargement import NOMS_FICHIERS, charger
from corroborai.config import (ANOMALIE, CHAMPS, CONFORME, JUSTIFIE, VERDICTS, Parametres)
from corroborai.config import champ as get_champ
from corroborai.ia import llm
from corroborai.modele import AGGRAVANT, EXPLICATIF
from corroborai.moteur import corroborer, niveau_priorite
from corroborai.regles import REGLES

DOSSIER = Path(__file__).parent / "data"
COULEUR = {CONFORME: "#0ca30c", JUSTIFIE: "#fab219", ANOMALIE: "#d03b3b"}
ICONE = {CONFORME: "✔", JUSTIFIE: "≈", ANOMALIE: "✘"}
NATURE = {EXPLICATIF: "🟡 explicatif", AGGRAVANT: "🔴 aggravant", "contexte": "⚪ contexte"}

st.set_page_config(page_title="CorroborIA – Loto-Québec", page_icon="🔎", layout="wide")


# ---------------------------------------------------------------- barre latérale
with st.sidebar:
    st.title("🔎 CorroborIA")
    st.caption(f"Système A – RH ↔ Système B – Temps · v{__version__}")
    src = st.radio("Données", ["Fichiers fournis (data/)", "Téléverser les extractions"])
    televerses = {}
    if src.startswith("Téléverser"):
        for cle, nom in NOMS_FICHIERS.items():
            f = st.file_uploader(nom, type=["xlsx"], key=f"up_{cle}")
            if f is not None:
                televerses[cle] = f.getvalue()
    st.divider()
    st.subheader("IA (niveau 3)")
    fournisseur_nom = st.selectbox("Moteur", ["auto", "anthropic", "ollama", "aucun"],
                                   help="auto : Claude si une clé API est définie, sinon arbitre local hors-ligne")
    cle_api = st.text_input("Clé API Anthropic (session uniquement)", type="password",
                            help="Conservée en mémoire pour cette session, jamais écrite sur disque.")
    modele = st.text_input("Modèle", value=os.environ.get("CORROBORAI_MODELE", llm.MODELE_ANTHROPIC_DEFAUT))
    pseudo = st.toggle("Pseudonymiser les matricules envoyés au LLM", value=True)
    st.divider()
    st.subheader("Paramètres des règles")
    mode = st.selectbox("R-DATE-DEBUT (hypothèse H1)", ["plus_recente", "plus_ancienne"],
                        format_func=lambda m: {"plus_recente": "Date la plus récente (validée)",
                                               "plus_ancienne": "Date la plus ancienne (littérale)"}[m])
    date_ref = st.date_input("Date de référence", value=date.today())
    lancer = st.button("▶ Lancer la corroboration", type="primary", use_container_width=True)


def executer():
    if cle_api:
        os.environ["ANTHROPIC_API_KEY"] = cle_api
    if televerses and len(televerses) < len(NOMS_FICHIERS):
        st.error("Téléversez les cinq fichiers ou utilisez les fichiers fournis.")
        return
    donnees = charger(fichiers=televerses) if televerses else charger(dossier=DOSSIER)
    params = Parametres(mode_date_debut=mode, date_reference=date_ref, fournisseur_llm=fournisseur_nom,
                        modele_llm=modele, pseudonymiser_llm=pseudo)
    try:
        fournisseur = llm.creer(fournisseur_nom, modele)
    except Exception as e:
        st.warning(f"LLM indisponible ({e}) → arbitre local hors-ligne.")
        fournisseur = None
    corrections = retroaction.charger()
    barre = st.progress(0.0, "Démarrage…")
    res = corroborer(donnees, params, corrections, fournisseur, progression=lambda m, p: barre.progress(p, m))
    barre.empty()
    st.session_state.update(res=res, donnees=donnees, corrections=corrections)


if lancer or "res" not in st.session_state:
    executer()
if "res" not in st.session_state:
    st.stop()

res = st.session_state.res
donnees = st.session_state.donnees
corrections = st.session_state.corrections
t = res.tableau()
s = res.stats()

# ---------------------------------------------------------------- en-tête
st.markdown("### Rapport de corroboration")
st.caption(f"Exécuté le {res.horodatage} · moteur IA : **{res.fournisseur}** · "
           f"{s['affectations_source']} affectations source / {s['affectations_cible']} cible · "
           "fichiers sources lus en lecture seule (empreintes SHA-256 dans le rapport)")
k = st.columns(5)
k[0].metric("Contrôles", s["total"])
k[1].metric(f"{ICONE[CONFORME]} Conformes", s[CONFORME])
k[2].metric(f"{ICONE[JUSTIFIE]} Écarts justifiés", s[JUSTIFIE])
k[3].metric(f"{ICONE[ANOMALIE]} Anomalies", s[ANOMALIE], help=f"{s['employes_anomalie']} employés touchés")
k[4].metric("Cas tranchés par l'IA", s["ambigus_ia"])

onglets = st.tabs(["🎯 Démo : 3 types de cas", "🚨 Anomalies", "≈ Écarts justifiés", "🤖 Analyse IA",
                   "🔍 Fiche d'explication", "📊 Vue d'ensemble", "🧪 Qualité & hypothèses", "⬇ Export"])


def fiche(id_: str, cle_form: str):
    """Fiche d'explication d'un constat : valeurs, règle officielle, trace, preuves, avis IA, rétroaction."""
    c = next(x for x in res.constats if x.id == id_)
    st.markdown(f"#### {ICONE.get(c.verdict, '')} {c.verdict} — {c.libelle} (`{c.champ_cible}`)")
    st.caption(f"{c.id} · matricule {c.matricule} · {c.affectation} · ligne source {c.ligne_source} · "
               f"ligne cible {c.ligne_cible}")
    a, b, d = st.columns(3)
    a.markdown(f"**Système A – RH**\n\n`{c.valeur_source}`")
    b.markdown(f"**Attendu selon la règle**\n\n`{c.valeur_attendue}`")
    d.markdown(f"**Système B – Temps**\n\n`{c.valeur_cible}`")
    st.markdown(f"**Décision :** {c.origine} · règle **{c.regle}** · confiance **{c.confiance:.0%}**"
                + (f" · priorité **{niveau_priorite(c)} ({c.priorite:.0f})**" if c.priorite else ""))
    st.info(c.justification)
    if c.action:
        st.markdown(f"**Action recommandée :** {c.action}")
    if c.avis_ia.get("diagnostic"):
        st.markdown(f"**Diagnostic IA** ({c.avis_ia.get('fournisseur_diagnostic', 'local')}) : "
                    f"{c.avis_ia['diagnostic']}")
    g, h = st.columns(2)
    with g:
        if c.regle in REGLES:
            st.markdown("**Règle officielle (Mapping.xlsx)**")
            try:
                for cell in get_champ(c.champ_cible).cellules:
                    feuille, coord = cell.split("!")
                    ligne = "".join(ch_ for ch_ in coord if ch_.isdigit())
                    vals = [donnees.mapping_cellules.get(f"{feuille}!{col}{ligne}", "") for col in "ABCD"]
                    st.caption(f"`{feuille} ligne {ligne}` — " + " | ".join(v for v in vals if v))
            except KeyError:
                pass
        st.markdown("**Trace du calcul**")
        st.code("\n".join(c.trace) or "—", language=None)
        if c.hypotheses:
            st.caption("Hypothèses : " + " ; ".join(c.hypotheses))
    with h:
        if c.signaux:
            st.markdown("**Preuves (analyse de motifs)**")
            for sg in c.signaux:
                st.markdown(f"- {NATURE.get(sg.nature, sg.nature)} `{sg.code}` — {sg.description}")
        if "local" in c.avis_ia:
            loc = c.avis_ia["local"]
            st.markdown(f"**Arbitre local :** {loc['heuristique']} → {loc['verdict']} ({loc['confiance']:.0%})")
        if "llm" in c.avis_ia:
            st.markdown(f"**LLM ({c.avis_ia['fournisseur']}) :** {c.avis_ia['llm']['verdict']} "
                        f"({c.avis_ia['llm']['confiance']:.0%}) · accord avec l'arbitre local : "
                        f"{'oui' if c.avis_ia.get('accord_local') else 'NON'}")
    with st.expander("Données ayant servi à la décision"):
        if c.ligne_source:
            st.markdown("Ligne source (Système A)")
            st.dataframe(donnees.source.iloc[[c.ligne_source - 2]].drop(columns=["_ligne_source"]),
                         hide_index=True)
        if c.ligne_cible:
            st.markdown("Ligne cible (Système B, colonnes non vides)")
            st.dataframe(donnees.cible.iloc[[c.ligne_cible - 2]].dropna(axis=1).drop(columns=["_ligne_cible"]),
                         hide_index=True)
        if c._ctx is not None and not c._ctx.detail.empty:
            st.markdown(f"Historique du détail du poste {c._ctx.src['CodePoste']}")
            st.dataframe(c._ctx.detail, hide_index=True)
    with st.form(f"retro_{cle_form}_{c.id}"):
        st.markdown("**Corriger ce verdict (rétroaction experte)**")
        x1, x2, x3 = st.columns([1, 1, 1])
        nv = x1.selectbox("Verdict expert", VERDICTS, index=VERDICTS.index(c.verdict) if c.verdict in VERDICTS else 0)
        portee = x2.selectbox("Portée", ["cas", "signature"], disabled=not c.ambigu,
                              help="« signature » crée une règle apprise appliquée aux cas ambigus semblables "
                                   f"({c.signature})")
        auteur = x3.text_input("Expert", value="expert.fonctionnel")
        com = st.text_input("Justification de l'expert")
        if st.form_submit_button("Enregistrer et relancer"):
            r = retroaction.ajouter(corrections, c, nv, com, auteur, portee if c.ambigu else "cas")
            retroaction.sauvegarder(corrections)
            st.success(f"Correction enregistrée{' — ' + r['id_regle'] if 'id_regle' in r else ''}.")
            executer()
            st.rerun()


# ---------------------------------------------------------------- 1. démo
with onglets[0]:
    st.markdown("Trois cas représentatifs tirés du jeu de données, avec la preuve complète.")
    def premier(filtre):
        x = t[filtre]
        return None if x.empty else x.iloc[0]["ID"]
    cas = {
        f"{ICONE[CONFORME]} Cas conforme": premier((t["Verdict"] == CONFORME) & (t["Champ cible"] == "onboardDate")),
        f"{ICONE[JUSTIFIE]} Écart justifié automatiquement": premier((t["Verdict"] == JUSTIFIE)
                                                                     & (t["Champ cible"] == "statusReasonCode")
                                                                     & (t["Valeur cible"] != "(vide)")),
        f"{ICONE[JUSTIFIE]} Écart justifié par l'IA": premier((t["Verdict"] == JUSTIFIE) &
                                                               (t["Champ cible"] == "positionName")),
        f"{ICONE[ANOMALIE]} Vraie anomalie (règle)": premier((t["Verdict"] == ANOMALIE)
                                                              & (t["Champ cible"] == "contractTypeCode")),
        f"{ICONE[ANOMALIE]} Vraie anomalie (IA)": premier((t["Verdict"] == ANOMALIE)
                                                           & (t["Champ cible"] == "assignmentStartDate")),
    }
    choix = st.radio("Cas", [k_ for k_, v in cas.items() if v], horizontal=True)
    fiche(cas[choix], "demo")

# ---------------------------------------------------------------- 2. anomalies
with onglets[1]:
    an = t[t["Verdict"] == ANOMALIE].sort_values("Priorité", ascending=False)
    st.markdown(f"**{len(an)} anomalies** sur {an['Matricule'].nunique()} employés, triées par priorité "
                "(impact métier × confiance, bonifié pour les problèmes systémiques).")
    sel = st.dataframe(an[["ID", "Niveau priorité", "Priorité", "Matricule", "Champ cible", "Valeur attendue",
                           "Valeur cible", "Confiance", "Origine de la décision", "Justification"]],
                       hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                       column_config={"Confiance": st.column_config.ProgressColumn(format="%.2f", min_value=0,
                                                                                   max_value=1),
                                      "Priorité": st.column_config.NumberColumn(format="%.0f")})
    if sel.selection.rows:
        fiche(an.iloc[sel.selection.rows[0]]["ID"], "an")
    else:
        st.caption("Sélectionnez une ligne pour afficher sa fiche d'explication.")

# ---------------------------------------------------------------- 3. justifiés
with onglets[2]:
    ju = t[t["Verdict"] == JUSTIFIE]
    f_r = st.multiselect("Règle", sorted(ju["Règle"].unique()))
    if f_r:
        ju = ju[ju["Règle"].isin(f_r)]
    resume = ju.groupby(["Champ cible", "Règle", "Origine de la décision"]).agg(
        Cas=("ID", "count"), Confiance_min=("Confiance", "min")).reset_index()
    st.dataframe(resume, hide_index=True, use_container_width=True)
    sel = st.dataframe(ju[["ID", "Matricule", "Champ cible", "Valeur source", "Valeur attendue", "Valeur cible",
                           "Confiance", "Règle", "Justification"]], hide_index=True, use_container_width=True,
                       on_select="rerun", selection_mode="single-row")
    if sel.selection.rows:
        fiche(ju.iloc[sel.selection.rows[0]]["ID"], "ju")

# ---------------------------------------------------------------- 4. IA
with onglets[3]:
    ia = t[t["Signature"] != ""]
    st.markdown(
        "Les écarts **non résolus par les règles** mais accompagnés d'au moins une preuve explicative sont "
        "regroupés par *signature* (champ + types de preuves) et soumis à l'IA avec leur dossier. "
        "L'arbitre local donne toujours un second avis ; un désaccord abaisse la confiance.")
    grp = ia.groupby(["Signature", "Verdict"]).agg(Cas=("ID", "count"), Confiance=("Confiance", "mean")).reset_index()
    st.dataframe(grp, hide_index=True, use_container_width=True)
    sel = st.dataframe(ia[["ID", "Matricule", "Champ cible", "Valeur attendue", "Valeur cible", "Verdict",
                           "Confiance", "Avis arbitre local", "Accord LLM/local", "Justification"]],
                       hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row")
    if sel.selection.rows:
        fiche(ia.iloc[sel.selection.rows[0]]["ID"], "ia")
    appr = retroaction.regles_apprises(corrections)
    if corrections:
        st.markdown("**Rétroaction experte enregistrée**")
        st.dataframe(pd.DataFrame(corrections)[["portee", "champ", "verdict_initial", "verdict", "commentaire",
                                                "auteur", "horodatage"]], hide_index=True)
    if appr:
        st.markdown("**Règles apprises**")
        for r in appr:
            st.markdown(f"- `{r['id_regle']}` {r['texte_regle']}")

# ---------------------------------------------------------------- 5. fiche
with onglets[4]:
    mats = sorted(t["Matricule"].unique())
    m = st.selectbox("Employé", mats, index=mats.index(t[t["Verdict"] == ANOMALIE]["Matricule"].iloc[0])
                     if s[ANOMALIE] else 0)
    sub = t[t["Matricule"] == m]
    mat = sub.pivot_table(index="Champ cible", columns="Affectation", values="Verdict", aggfunc="first")
    ordre = [c.cible for c in CHAMPS] + ["(affectation entière)"]
    mat = mat.reindex([o for o in ordre if o in mat.index])
    st.dataframe(mat.map(lambda v: f"{ICONE.get(v, '')} {v}" if isinstance(v, str) else ""),
                 use_container_width=True)
    ids = sub[sub["Verdict"] != CONFORME]["ID"].tolist() or sub["ID"].tolist()
    lib = {r["ID"]: f"{r['ID']} · {r['Champ cible']} · {r['Verdict']}" for _, r in sub.iterrows()}
    choix = st.selectbox("Constat", sub["ID"].tolist(), index=sub["ID"].tolist().index(ids[0]),
                         format_func=lambda i: lib[i])
    fiche(choix, "fiche")

# ---------------------------------------------------------------- 6. vue d'ensemble
with onglets[5]:
    pc = t.groupby(["Champ cible", "Verdict"]).size().reset_index(name="Contrôles")
    ordre_champs = [c.cible for c in CHAMPS] + ["(affectation entière)"]
    graph = alt.Chart(pc).mark_bar(stroke="white", strokeWidth=2, cornerRadiusEnd=4).encode(
        y=alt.Y("Champ cible:N", sort=ordre_champs, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=220)),
        x=alt.X("Contrôles:Q", title="Nombre de contrôles"),
        color=alt.Color("Verdict:N", scale=alt.Scale(domain=list(COULEUR), range=list(COULEUR.values())),
                        legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("Verdict:N"),
        tooltip=["Champ cible", "Verdict", "Contrôles"],
    ).properties(height=26 * 26, title="Verdicts par champ corroboré")
    st.altair_chart(graph, use_container_width=True)
    o = pd.Series(s["origines"]).rename_axis("Origine").reset_index(name="Décisions")
    st.markdown("**Qui a décidé ?** (séparation déterministe / IA / expert)")
    st.dataframe(o, hide_index=True)
    st.markdown("**Table des verdicts par champ**")
    st.dataframe(pc.pivot(index="Champ cible", columns="Verdict", values="Contrôles").fillna(0).astype(int)
                 .reindex([c for c in ordre_champs if c in pc["Champ cible"].unique()]), use_container_width=True)

# ---------------------------------------------------------------- 7. qualité
with onglets[6]:
    st.markdown("**Validation empirique des hypothèses**")
    st.dataframe(pd.DataFrame(res.validation_hypotheses), hide_index=True)
    st.markdown("**Observations de qualité des données** (hors verdict : les deux systèmes peuvent concorder)")
    st.dataframe(pd.DataFrame(res.observations), hide_index=True, use_container_width=True)
    st.markdown("**Appariement des affectations**")
    st.dataframe(pd.DataFrame([vars(p) for p in res.paires]), hide_index=True, use_container_width=True)
    with st.expander("Journal d'exécution"):
        st.code("\n".join(res.journal), language=None)

# ---------------------------------------------------------------- 8. export
with onglets[7]:
    st.download_button("⬇ Rapport Excel (multi-onglets)", rapport.exporter_excel(res, corrections=corrections),
                       "rapport_corroboration.xlsx", type="primary")
    st.download_button("⬇ Détail CSV", rapport.exporter_csv(res), "rapport_corroboration.csv")
    st.caption("Onglets : Synthèse, Par champ, Anomalies, Écarts justifiés, Analyse IA, Détail complet, "
               "Appariement, Observations qualité, Hypothèses, Catalogue des règles, Journal.")
