"""Normalisation des valeurs avant comparaison (niveau 1 du moteur).

Chaque fonction retourne la valeur normalisée ET le code de la normalisation
appliquée (N-xx), afin que le rapport puisse indiquer pourquoi deux valeurs
brutes différentes sont considérées identiques.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

# Catalogue des normalisations techniques (traçables dans le rapport)
NORMALISATIONS = {
    "N-VIDE": "Valeurs vides unifiées (NaN, '', 'null', 'None' → vide)",
    "N-DATE": "Dates ramenées au format AAAA-MM-JJ (heure, fuseau et format ISO 'Z' ignorés)",
    "N-NOMBRE": "Nombres comparés numériquement (40 = 40.0, codes sans zéros de tête)",
    "N-BOOL": "Booléens unifiés (Oui/Non, true/false, 1/0)",
    "N-ENCODAGE": "Réparation d'encodage (ex. « complÃ¨te » → « complète »)",
    "N-ESPACES": "Espaces superflus et casse ignorés pour les textes",
    "N-ACCENTS": "Accents retirés (règle du mapping : « Enlever les accents pour le nom et prénom »)",
    "N-EXCEL-SERIE": "Date au format numéro de série Excel convertie (origine 1899-12-30)",
}

_VIDES = {"", "nan", "nat", "none", "null", "<na>"}


def est_vide(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if v is pd.NaT:
        return True
    return isinstance(v, str) and v.strip().lower() in _VIDES


def reparer_encodage(texte: str) -> tuple[str, bool]:
    """Répare le mojibake UTF-8 lu en Latin-1 (« Ã¨ » → « è »)."""
    if not isinstance(texte, str) or not re.search(r"[ÃÂ][\x80-\xbf¡-ÿ]", texte):
        return texte, False
    try:
        repare = texte.encode("latin-1").decode("utf-8")
        return repare, repare != texte
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texte, False


def sans_accents(texte: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def serie_excel_vers_date(n: int | float | str) -> date:
    return (datetime(1899, 12, 30) + timedelta(days=int(float(n)))).date()


def vers_date(v: Any) -> date | None:
    if est_vide(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if re.fullmatch(r"\d{4,5}(\.0)?", s):  # numéro de série Excel
        return serie_excel_vers_date(s)
    ts = pd.to_datetime(s, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def vers_nombre(v: Any) -> float | None:
    if est_vide(v):
        return None
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return None


def vers_bool(v: Any) -> bool | None:
    if est_vide(v):
        return None
    s = str(v).strip().lower()
    if s in {"oui", "true", "1", "1.0", "o", "y", "yes", "vrai"}:
        return True
    if s in {"non", "false", "0", "0.0", "n", "no", "faux"}:
        return False
    return None


def normaliser(v: Any, type_champ: str) -> tuple[Any, list[str]]:
    """Normalise une valeur selon son type. Retourne (valeur, [codes N-xx])."""
    codes: list[str] = []
    if est_vide(v):
        return None, []
    if type_champ == "date":
        d = vers_date(v)
        brut = str(v)
        if d is not None and brut != d.isoformat():
            codes.append("N-DATE")
        return d, codes
    if type_champ in ("nombre", "code"):
        n = vers_nombre(v)
        if n is None:
            return str(v).strip(), codes
        if str(v).strip() != (str(int(n)) if n.is_integer() else str(n)):
            codes.append("N-NOMBRE")
        return n, codes
    if type_champ == "bool":
        b = vers_bool(v)
        if str(v).strip().lower() not in {"true", "false"}:
            codes.append("N-BOOL")
        return b, codes
    # texte
    s = str(v)
    s2, repare = reparer_encodage(s)
    if repare:
        codes.append("N-ENCODAGE")
    s3 = re.sub(r"\s+", " ", s2).strip()
    if s3 != s2:
        codes.append("N-ESPACES")
    return s3, codes


def egal(a: Any, b: Any, type_champ: str) -> bool:
    """Égalité après normalisation (textes : insensible à la casse)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if type_champ == "texte" or isinstance(a, str) or isinstance(b, str):
        return str(a).casefold() == str(b).casefold()
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9
    return a == b


def afficher(v: Any) -> str:
    """Représentation lisible d'une valeur normalisée pour le rapport."""
    if v is None:
        return "(vide)"
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:g}"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def brut(v: Any) -> str:
    """Représentation brute (avant normalisation) pour la preuve."""
    if est_vide(v):
        return "(vide)"
    return str(v)
