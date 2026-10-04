"""Configuration déclarative : champs à corroborer (uniquement ceux du mapping),
règle applicable, cellule du fichier Mapping.xlsx qui la documente et poids
métier utilisé pour prioriser les anomalies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# --- Verdicts -----------------------------------------------------------------
CONFORME = "Conforme"
JUSTIFIE = "Écart justifié"
ANOMALIE = "Anomalie"
VERDICTS = (CONFORME, JUSTIFIE, ANOMALIE)

# --- Origine de la décision (déterministe vs IA vs expert) -------------------
ORIG_DIRECT = "Déterministe – comparaison directe"
ORIG_NORMALISATION = "Déterministe – normalisation"
ORIG_REGLE = "Déterministe – règle métier"
ORIG_IA_LLM = "IA – LLM"
ORIG_IA_LOCAL = "IA – arbitre local (hors-ligne)"
ORIG_EXPERT = "Expert – correction manuelle"
ORIG_APPRIS = "Règle apprise (rétroaction expert)"

NIVEAUX = {
    ORIG_DIRECT: "1 – Comparaison brute",
    ORIG_NORMALISATION: "1 – Comparaison brute",
    ORIG_REGLE: "2 – Règles métier",
    ORIG_IA_LLM: "3 – Analyse IA",
    ORIG_IA_LOCAL: "3 – Analyse IA",
    ORIG_EXPERT: "4 – Validation experte",
    ORIG_APPRIS: "4 – Validation experte",
}


@dataclass(frozen=True)
class Champ:
    cible: str
    sources: tuple[str, ...]
    libelle: str
    regle: str
    cellules: tuple[str, ...]
    type: str = "texte"         # texte | code | nombre | date | bool
    impact: float = 0.5         # 0–1 : impact métier d'une anomalie (paie, accès, etc.)
    derive: bool = False        # champ cible dérivé sans champ source direct (« - » au mapping)


M = "Mapping!"
S = "Règles situation d'emploi!"

CHAMPS: tuple[Champ, ...] = (
    Champ("personId", ("Matricule",), "Code de l'employé", "R-DIRECT", (M + "B8",), "code", 1.0),
    Champ("givenName", ("PrénomUsuel",), "Prénom", "R-DIRECT", (M + "B2",), "texte", 0.5),
    Champ("surname", ("NomFamille",), "Nom", "R-DIRECT", (M + "B3",), "texte", 0.5),
    Champ("contactEmail", ("PrénomUsuel", "NomFamille", "Matricule"), "Courriel", "R-EMAIL",
          (M + "D4", M + "D6"), "texte", 0.4),
    Champ("onboardDate", ("DateEmbaucheRécente",), "Date d'embauche", "R-DIRECT", (M + "B7",), "date", 0.8),
    Champ("siteName", ("LibelléSite",), "Emplacement", "R-DIRECT", (M + "B9",), "texte", 0.6),
    Champ("siteCode", ("CodeSite",), "Code d'emplacement", "R-DIRECT", (M + "B10",), "code", 0.7),
    Champ("divisionId", ("CodeDirection",), "Département", "R-DIRECT", (M + "B11",), "code", 0.7),
    Champ("divisionName", ("CodeDirection", "LibelléDirection"), "Nom du département", "R-CONCAT-DIV",
          (M + "D12",), "texte", 0.4),
    Champ("divisionCode", ("CodeImputation",), "Code de département (imputation)", "R-DIRECT",
          (M + "B13",), "code", 0.7),
    Champ("positionId", ("CodeEmploi",), "Id du rôle", "R-DIRECT", (M + "B14",), "code", 0.8),
    Champ("positionName", ("CodeEmploi", "IntituléEmploi"), "Nom du rôle", "R-CONCAT-POS",
          (M + "D15",), "texte", 0.4),
    Champ("positionCode", ("CodeEmploi",), "Code du rôle", "R-DIRECT", (M + "B16",), "code", 0.8),
    Champ("statusReasonCode", ("CodeStatutEmploi", "CodeRaisonStatut", "CodeSuspensionAccès"),
          "Congé / absence long terme (code Remphor)", "R-SIT-CAD",
          (M + "D17", M + "D19", S + "A3", S + "C3"), "code", 0.9),
    Champ("expectedReturnDate", ("DateRetourAnticipée", "CodeSuspensionAccès"), "Date de retour prévue",
          "R-SIT-CADP", (M + "D22", S + "D3"), "date", 0.8),
    Champ("detailedStatus", ("CodeSuspensionAccès", "CodeRaisonStatut"), "Situation d'emploi",
          "R-SIT-STATUT", (M + "D23", S + "A2", S + "B2", S + "A3", S + "B3"), "texte", 0.9, derive=True),
    Champ("contractTypeCode", ("CatégorieEmploi", "EstPermanent", "EstTempsPlein"), "Type d'employé",
          "R-CONTRAT", (M + "D24",), "texte", 1.0),
    Champ("isPrimaryAssignment", ("TypeAffectation",), "Affectation primaire", "R-AFFECT",
          (M + "D41", M + "D45"), "bool", 0.8),
    Champ("isTemporaryAssignment", ("TypeAffectation",), "Affectation temporaire", "R-AFFECT",
          (M + "D43", M + "D45"), "bool", 0.8),
    Champ("assignmentStartDate", ("DateEntréePoste", "CodePoste", "CodeDirection"), "Date d'effet du poste",
          "R-DATE-DEBUT", (M + "D46", M + "D48"), "date", 0.7),
    Champ("payGradeId", ("ÉchelleSalariale",), "Groupe de rémunération", "R-DIRECT", (M + "B49",), "code", 1.0),
    Champ("weeklyHoursOverride", ("HeuresNormeHebdo",), "Heures / semaine", "R-DIRECT", (M + "B51",),
          "nombre", 0.9),
    Champ("dailyHoursOverride", ("HeuresNormeQuotidienne",), "Heures / jour", "R-DIRECT", (M + "B52",),
          "nombre", 0.9),
    Champ("assignmentEndDate", ("DateSortiePoste", "CodePoste", "CodeDirection"), "Date d'expiration du poste",
          "R-DATE-FIN", (M + "D53",), "date", 0.7),
    Champ("termEndDate", ("DateSortiePoste", "CodePoste", "CodeDirection"), "Fin du détail du poste",
          "R-DATE-FIN", (M + "D56", M + "D58"), "date", 0.6),
)

# Champs présents dans les extractions mais NON corroborés (hors mapping ou sans cible)
HORS_PERIMETRE = {
    "LibelléImputation": "Pas de champ cible dans le mapping (« - »)",
    "DateEffetRaison": "Présent dans le mapping sans champ cible",
    "siteId, termStartDate, activityStatus, customAttribute_*": "Champs cible absents du mapping",
    "IdentifiantResponsable, NomResponsable, CodeQuart, IntituléPoste": "Champs source absents du mapping "
    "(CodeQuart sert uniquement de contexte à l'IA)",
}


@dataclass
class Parametres:
    """Paramètres d'exécution (consignés dans le rapport)."""
    # Interprétation de la règle R-DATE-DEBUT (voir README, hypothèse H1)
    #  "plus_recente" : max(date unité adm., date d'entrée poste)  ← validée empiriquement
    #  "plus_ancienne": min(...) ← lecture littérale du mapping
    mode_date_debut: str = "plus_recente"
    date_reference: date = date.today()
    ia_active: bool = True
    fournisseur_llm: str = "auto"      # auto | anthropic | ollama | aucun
    modele_llm: str | None = None
    pseudonymiser_llm: bool = True     # minimisation des données envoyées au LLM
    seuil_transcodage: float = 0.9     # part minimale des lignes pour un écart « systémique »


def champ(nom: str) -> Champ:
    for c in CHAMPS:
        if c.cible == nom:
            return c
    raise KeyError(nom)
