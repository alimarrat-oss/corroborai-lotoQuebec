"""Interface en ligne de commande.

    python -m corroborai --donnees data --sortie sorties
    python -m corroborai --ia anthropic --modele claude-sonnet-5-5
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import rapport, retroaction
from .chargement import charger, verifier_integrite
from .config import ANOMALIE, Parametres
from .ia import llm
from .moteur import corroborer, niveau_priorite


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="corroborai", description="Corroboration Système A – RH ↔ Système B – Temps")
    ap.add_argument("--donnees", default="data", help="dossier des extractions (lecture seule)")
    ap.add_argument("--sortie", default="sorties", help="dossier des rapports générés")
    ap.add_argument("--ia", default="auto", choices=["auto", "anthropic", "ollama", "aucun"],
                    help="fournisseur LLM pour le niveau 3 (auto : Claude si ANTHROPIC_API_KEY, sinon arbitre local)")
    ap.add_argument("--modele", default=None, help="identifiant du modèle LLM")
    ap.add_argument("--mode-date-debut", default="plus_recente", choices=["plus_recente", "plus_ancienne"])
    ap.add_argument("--date-reference", default=None, help="AAAA-MM-JJ (défaut : aujourd'hui)")
    ap.add_argument("--corrections", default=str(retroaction.FICHIER_DEFAUT), help="fichier de rétroaction experte")
    ap.add_argument("--sans-pseudonymisation", action="store_true", help="envoyer les matricules réels au LLM")
    a = ap.parse_args(argv)

    params = Parametres(mode_date_debut=a.mode_date_debut, fournisseur_llm=a.ia, modele_llm=a.modele,
                        pseudonymiser_llm=not a.sans_pseudonymisation,
                        date_reference=date.fromisoformat(a.date_reference) if a.date_reference else date.today())
    donnees = charger(dossier=a.donnees)
    fournisseur = llm.creer(a.ia, a.modele)
    corrections = retroaction.charger(a.corrections)
    res = corroborer(donnees, params, corrections, fournisseur,
                     progression=lambda m, p: print(f"[{p:>4.0%}] {m}", file=sys.stderr))

    sortie = Path(a.sortie)
    xlsx = sortie / "rapport_corroboration.xlsx"
    rapport.exporter_excel(res, xlsx, corrections)
    rapport.exporter_csv(res, sortie / "rapport_corroboration.csv")
    integ = verifier_integrite(a.donnees, donnees.empreintes)

    s = res.stats()
    print(f"\nMoteur IA : {res.fournisseur}")
    print(f"Contrôles : {s['total']}  |  Conformes : {s['Conforme']}  |  Écarts justifiés : {s['Écart justifié']}"
          f"  |  Anomalies : {s['Anomalie']}  (cas ambigus tranchés par l'IA : {s['ambigus_ia']})")
    print("\nAnomalies à investiguer (par priorité) :")
    for c in sorted((c for c in res.constats if c.verdict == ANOMALIE), key=lambda c: -c.priorite):
        print(f"  {c.id}  [{niveau_priorite(c):7}] {c.matricule:>8}  {c.champ_cible:<22} "
              f"source={c.valeur_attendue!s:<24.24} cible={c.valeur_cible!s:<24.24} ({c.origine}, {c.confiance:.0%})")
    print(f"\nRapport : {xlsx}")
    print("Fichiers sources inchangés : " + ("oui" if all(integ.values()) else f"NON {integ}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
