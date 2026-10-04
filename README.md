# CorroborIA – corroboration intelligente RH ↔ Temps

Défi Loto-Québec · CorroborIA. Prototype hybride **règles + IA** qui compare les extractions du **Système A – RH** et du **Système B – Temps**, applique les règles métier du mapping, fait trancher les cas ambigus par l'IA et produit un **rapport explicable** : chaque verdict indique la règle appliquée, les preuves, l'origine de la décision et un niveau de confiance.

> **Résultat sur le jeu fourni** : une comparaison « identique / différent » naïve signale **211 différences** sur 550 contrôles. CorroborIA en retient **16 vraies anomalies** (11 employés). Les autres se répartissent en **383 contrôles conformes** et **152 écarts justifiés**, chacun avec sa justification. La complétude des affectations est aussi contrôlée (551 contrôles au total).

---

## 1. Démarrage rapide

```bash
pip install -r requirements.txt

# Ligne de commande : produit sorties/rapport_corroboration.xlsx et .csv
python -m corroborai --donnees data --sortie sorties

# Démonstrateur web (téléverser les fichiers, lancer, explorer, corriger, exporter)
streamlit run app.py

# Tests (25 tests : règles, appariement, IA, rétroaction, rapport, lecture seule)
python -m pytest -q tests

# Notebook explicatif (exploration, choix d'approche, résultats)
jupyter notebook notebooks/corroboration.ipynb
```

**Activer le LLM (niveau 3)** – facultatif, la solution fonctionne entièrement hors ligne sans clé :

```bash
export ANTHROPIC_API_KEY=...                  # API Claude
export CORROBORAI_MODELE=claude-sonnet-5-5    # facultatif (valeur par défaut)
python -m corroborai --ia anthropic

# ou 100 % local (aucune donnée ne quitte le poste) :
export OLLAMA_HOST=http://localhost:11434
python -m corroborai --ia ollama --modele llama3.1
```

Options utiles : `--mode-date-debut plus_ancienne` (lecture littérale de la règle, voir H1), `--date-reference AAAA-MM-JJ`, `--sans-pseudonymisation`, `--ia aucun`.

---

## 2. Architecture

```
data/ (lecture seule, SHA-256 vérifié)
  │
  ▼
chargement.py ── normalisation.py ── appariement.py (algorithme hongrois, multi-affectations)
  │
  ▼
moteur.py
  ├─ Niveau 1  Comparaison brute + normalisation (N-DATE, N-BOOL, N-ENCODAGE…) ─► Conforme
  ├─ Niveau 2  Règles métier du mapping (regles.py, R-xx)                        ─► Conforme / Écart justifié / Anomalie
  │            └─ écart non expliqué ─► signaux.py (analyse de motifs sur tout le jeu)
  │                 ├─ aucune preuve explicative ─► Anomalie (déterministe)
  │                 └─ preuve explicative ou attendu incalculable ─► CAS AMBIGU
  ├─ Niveau 3  ia/arbitre.py : LLM (Claude / Ollama) + arbitre local, par grappe ─► Écart justifié / Anomalie + confiance
  └─ Niveau 4  retroaction.py : correction experte (cas) ou règle apprise (signature)
  │
  ▼
rapport.py (Excel 11 onglets + CSV)   ·   app.py (Streamlit)   ·   __main__.py (CLI)
```

| Module | Rôle |
|---|---|
| `config.py` | Les 25 champs corroborés (uniquement ceux du mapping), la règle de chaque champ, la cellule de `Mapping.xlsx` qui la documente et un poids d'impact métier |
| `chargement.py` | Lecture binaire des 5 fichiers, empreintes SHA-256 avant et après, extraction *détail du poste* (CSV dans une cellule, dates en numéro de série Excel) |
| `normalisation.py` | Normalisations traçables N-xx |
| `appariement.py` | Paire chaque affectation source avec une affectation cible (emploi, type P/A/S, date, unité) |
| `regles.py` | Calcul de la valeur attendue + **trace lisible** du calcul |
| `signaux.py` | Preuves calculées à l'échelle du jeu de données |
| `ia/arbitre.py`, `ia/llm.py`, `ia/local.py` | Arbitrage IA, fournisseurs LLM, arbitre local hors ligne |
| `retroaction.py` | Boucle experte et règles apprises |
| `moteur.py` | Orchestration, priorisation, observations de qualité, validation des hypothèses |

---

## 3. Règles prises en charge

| Règle | Champs cible | Source (mapping) |
|---|---|---|
| `R-DIRECT` | personId, givenName, surname, onboardDate, siteName, siteCode, divisionId, divisionCode, positionId, positionCode, payGradeId, weeklyHoursOverride, dailyHoursOverride | lignes 2–16, 49–52 |
| `R-EMAIL` | contactEmail : 1re lettre du prénom + nom + 3 derniers chiffres du code + `@loto-quebec.com`, sans accents | D4, D6 |
| `R-CONCAT-DIV` | divisionName = Unité adm. + « - » + desc. | D12 |
| `R-CONCAT-POS` | positionName = Emploi + « - » + desc. | D15 |
| `R-SIT-STATUT` | detailedStatus : code d'accès 00/01 → Actif ; 02/03/06/07 → Absence complète | D23 + onglet *Règles situation d'emploi* |
| `R-SIT-CAD` | statusReasonCode : code Remphor du motif (jointure `CodeRaisonStatut`) si absence, vide si actif | D17, D19 |
| `R-SIT-CADP` | expectedReturnDate : `DateRetourAnticipée` si absence, vide sinon | D22 |
| `R-CONTRAT` | contractTypeCode : table EMPTP_CD / PERM_IND / FT_IND → JWN, XFLR, KELH, WHX, CEGQ, CNZC, RMQ, JAW, TRSY | D24 |
| `R-AFFECT` | isPrimaryAssignment / isTemporaryAssignment : P → primaire, A → temporaire, S → les deux à false | D41–D45 |
| `R-DATE-DEBUT` | assignmentStartDate : date d'entrée au poste combinée à la date où l'unité adm. courante est devenue applicable (détection du changement dans l'historique, sinon MIN EFFDT) | D46, D48 |
| `R-DATE-FIN` | assignmentEndDate, termEndDate : la plus ancienne entre `DateSortiePoste` et la fin de l'unité courante (prochain détail − 1 jour si l'unité change) | D53, D56–D58 |
| `R-COMPLETUDE` | chaque affectation source doit exister dans la cible (et inversement) | implicite |

Hors périmètre, selon le mapping : `LibelléImputation` et `DateEffetRaison` (pas de champ cible), ainsi que `siteId`, `termStartDate`, `activityStatus` et `customAttribute_*` (absents du mapping). La liste complète figure dans l'onglet *Catalogue des règles* du rapport.

---

## 4. Utilisation de l'IA

L'IA ne remplace pas les règles. Elle intervient **uniquement là où une règle statique ne suffit pas**, et chaque verdict indique s'il vient d'une règle ou de l'IA.

1. **Analyse de motifs à l'échelle du jeu de données** (`signaux.py`). Pour chaque écart non expliqué, des preuves sont calculées et classées *explicatives*, *aggravantes* ou *contextuelles* :
   `S-TRANSCODAGE` (correspondance 1-pour-1 stable sur ≥ 90 % des lignes), `S-PREFIXE-ENV` (préfixe d'environnement commun, p. ex. `dev-08-v2_`), `S-STRUCTURE-CONFORME`, `S-DEFAUT-CIBLE` (valeur constante dans la cible malgré une source variée), `S-VALEUR-DETAIL-POSTE`, `S-DETAIL-SANS-CHANGEMENT-UNITE`, `S-QUART-COHERENT`, `S-VALEUR-SOURCE-EXPLICITE`, `S-PERMUTATION` (valeurs croisées entre deux employés), `S-INCOHERENCE-INTERNE` (code ≠ libellé dans la cible), `S-TYPE-ABSENT-CIBLE`.
2. **Arbitrage par LLM** (`ia/arbitre.py`). Les cas ambigus sont regroupés par *signature* (champ + types de preuves) et envoyés **par grappe** avec un dossier de preuves : texte officiel du mapping, trace du calcul, valeurs et signaux. Le LLM répond dans un **schéma JSON imposé** (appel d'outil forcé) : verdict, confiance, justification qui cite les signaux, action recommandée. Les réponses sont validées, et une sortie invalide déclenche un repli sur l'arbitre local. Température 0 et cache sur disque assurent la reproductibilité.
3. **Second avis indépendant**. Un arbitre local explicable (heuristiques A1–A6) se prononce toujours. Un **désaccord** avec le LLM plafonne la confiance à 50 % et marque le cas « à revoir ».
4. **Garde-fous**. Le LLM ne peut **jamais** modifier un verdict déterministe. Pour les anomalies déterministes, il se limite à un *diagnostic* : cause probable et action de correction.
5. **Priorisation**. Score = impact métier du champ × confiance, avec un bonus pour les problèmes systémiques (permutation, type d'affectation non transmis) et pour les désaccords. Un écart justifié avec une confiance < 80 % reçoit aussi une priorité (« à confirmer »).
6. **Apprentissage par rétroaction** (bonus). Un expert corrige un verdict dans l'application :
   - portée **cas** : la correction s'applique à ce cas précis ;
   - portée **signature** : une **règle apprise** `R-APP-nnn` est créée et appliquée automatiquement aux cas ambigus de même signature.

   Les corrections servent aussi d'exemples (*few-shot*) dans les prompts.
7. **Confidentialité**. Les matricules sont **pseudonymisés** avant l'envoi au LLM. La structure de la règle courriel est préservée et un test vérifie qu'aucun matricule réel n'est transmis. Le mode Ollama permet un traitement 100 % local.

> Pendant le développement, aucune clé API n'était disponible : le rapport fourni dans `sorties/` a été produit par l'**arbitre local hors ligne**. Le chemin LLM est couvert par des tests avec un client simulé (désaccord, sortie invalide, pseudonymisation). Avec une clé, `--ia anthropic` l'active sans autre changement.

---

## 5. Résultats sur le jeu fourni

**Les 16 anomalies à investiguer**

| Priorité | Employé(s) | Champ | Constat | Décision |
|---|---|---|---|---|
| Haute | 1545850 | affectation entière | L'affectation **temporaire** (poste 45985) est absente de la cible. Aucune affectation temporaire n'existe dans la cible (0/22) : problème systémique de l'interface | Règle R-COMPLETUDE |
| Haute | 2762457 ↔ 4625374, 3712987 ↔ 7254364 | contractTypeCode | Codes de contrat **permutés** entre deux paires d'employés (JWN/WHX, JWN/XFLR) | Règle R-CONTRAT + signal de permutation |
| Haute | 2911996, 4402456, 7683990 (P) | heures sem./jour | 35 h/7 h et 36 h/7,2 h dans le système A (quarts 8 et 9) **remplacées par la valeur par défaut** 40 h/8 h | IA (A3) |
| Moyenne | 9989151, 3241002, 4402456 | assignmentStartDate | La cible a repris la date du **dernier détail du poste**, qui reflète un changement de gestionnaire et non un changement d'unité administrative | IA (A4) |
| Moyenne | 6035643 ↔ 3241002 | siteName | Libellés d'emplacement **permutés**. Dans la cible, le libellé contredit le code | Comparaison directe + signaux |

**Écarts justifiés automatiquement (152)**

- **Par règle** : code de contrat (`V/Oui/Oui → JWN`), code d'absence Remphor (`807 → 170`), « pas de code d'absence » pour les actifs, booléens d'affectation (`P → true/false`), libellé de département concaténé et formaté (`00397-UnitAdmin00397`).
- **Par l'IA** :
  - `contactEmail` (22 cas) : préfixe d'environnement `dev-08-v2_` et identifiant transcodé de façon stable, structure de la règle respectée ;
  - `positionName` (22 cas) : transcodage 1-pour-1 des codes d'emploi sur 100 % des lignes ;
  - heures de 3712987 : source vide comblée par la valeur contractuelle du poste et du quart, priorité « à confirmer ».

**Observations de qualité** (hors verdict) : motifs absents de la table de référence (703, 697, 651), absences dont la date de retour prévue est dépassée de 11 à 16 ans, heures vides dans le système A, historiques de poste antérieurs à l'embauche (dates probablement décalées par l'anonymisation).

---

## 6. Hypothèses (traçées dans le rapport)

| # | Hypothèse | Justification |
|---|---|---|
| **H1** | R-DATE-DEBUT retient la date **la plus récente** entre la date de l'unité courante et `DateEntréePoste` | La lecture littérale (« la plus ancienne ») concorde avec **0/22** lignes cibles, la lecture « plus récente » avec **19/22**. Les 3 lignes restantes sont des anomalies. Cette lecture est aussi symétrique avec la règle de fin (« plus ancienne » des dates de fin). Le mode est paramétrable |
| H2 | « Code » dans R-EMAIL = matricule ; comparaison insensible à la casse | Mapping D4 |
| H3 | Codes formatés sur 5 positions (unité adm.) et 4 positions (emploi) dans les libellés concaténés | Format observé dans le référentiel (`UnitAdmin00397`) |
| H4 | Code de traitement des accès = `CodeSuspensionAccès`, recoupé avec `CodeGestionAccès` du motif ; si le motif est introuvable, on se fie à la source | Onglet *Jointure – Motif des situations* |
| H5 | Les différences de format, d'encodage ou de type ne sont pas des écarts (verdict conforme, normalisation citée) | Consignes : « normaliser les données lorsque nécessaire » |
| H6 | « Prochain détail du poste » s'évalue par rapport à une date de référence (par défaut aujourd'hui) | R-DATE-FIN |
| H7 | Pour un actif, un `statusReasonCode` vide alors que `CodeStatutEmploi=1` est un écart **justifié** par la table de situation | Mapping D17 |

## 7. Limites

- Jeu de données de 23 lignes : les seuils des signaux (p. ex. 90 % pour un transcodage) devront être recalibrés sur un volume réel.
- L'arbitre local repose sur des heuristiques explicites. Il est robuste et explicable, mais ne généralise pas à des motifs jamais vus : c'est le rôle du LLM et de la boucle de rétroaction.
- Les verdicts « écart justifié » obtenus par l'IA sur `contactEmail` et `positionName` supposent un artefact d'anonymisation ou d'environnement. Ils sont marqués « à confirmer » et un expert peut les basculer en anomalie d'un clic (règle apprise).
- La rétroaction est stockée dans un fichier JSON local (`retroaction/corrections.json`), sans gestion multi-utilisateur.

## 8. Contraintes respectées

- **Lecture seule** : fichiers ouverts en mode binaire, aucune écriture. Les empreintes SHA-256 avant et après sont comparées et consignées dans l'onglet *Synthèse*.
- **Explicabilité** : chaque ligne du rapport contient la valeur source, la valeur attendue, la valeur cible, la règle avec sa référence au mapping, la trace du calcul, les preuves, le verdict, la confiance, l'origine (déterministe / IA / expert) et l'action recommandée.
- **Déterministe vs IA** : colonnes *Niveau de décision* et *Origine de la décision*.
- **Confidentialité** : pseudonymisation avant tout appel externe et option 100 % locale (Ollama).

## 9. Rapport exporté (`sorties/rapport_corroboration.xlsx`)

Onglets : Synthèse · Par champ · **Anomalies** (triées par priorité) · Écarts justifiés · Analyse IA · Détail complet · Appariement · Observations qualité · Hypothèses · Catalogue des règles · Journal. Le même détail existe en CSV (`;`, UTF-8).

## 10. Démonstration (3 types de cas)

Dans l'application, l'onglet **🎯 Démo** affiche la fiche complète de :

- un cas **conforme** (date d'embauche, normalisation de format) ;
- un **écart justifié automatiquement** (code d'absence 807 → 170, et le transcodage `positionName` tranché par l'IA) ;
- une **vraie anomalie** (code de contrat permuté, et la date d'effet tranchée par l'IA).

Le notebook (section 7) reproduit les mêmes cas.

## 11. Outils, sources et modèles utilisés

- **Langage et bibliothèques** : Python 3.11+, pandas, NumPy, SciPy (`linear_sum_assignment`), openpyxl, Streamlit, Altair, pytest, nbformat.
- **IA** : API Claude d'Anthropic via le SDK `anthropic` (modèle par défaut `claude-sonnet-5-5`, configurable) ; Ollama en option pour un modèle local (p. ex. `llama3.1`).
- **Données** : uniquement les fichiers anonymisés fournis par Loto-Québec (`data/`).
- **Assistance au développement** : le code et la documentation ont été produits avec l'aide de Claude (Anthropic).

## 12. Structure du dépôt

```
app.py                      démonstrateur Streamlit
corroborai/                 package (moteur, règles, IA, rapport, CLI)
data/                       extractions fournies (lecture seule)
notebooks/corroboration.ipynb
tests/test_corroboration.py
sorties/                    rapport généré (xlsx, csv)
retroaction/                corrections expertes (créé à l'usage)
```
