"""Chargement des extractions en LECTURE SEULE.

- Les fichiers sont lus en mémoire (mode binaire 'rb') ; aucune écriture n'est faite.
- Une empreinte SHA-256 est calculée avant et après l'analyse pour prouver
  que les fichiers d'origine n'ont pas été modifiés (consignée dans le rapport).
- Toutes les colonnes sont lues comme texte (dtype=str) pour préserver les
  valeurs brutes (zéros de tête, formats de dates) qui servent de preuve.
"""
from __future__ import annotations

import hashlib
import io
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import openpyxl
import pandas as pd

from .normalisation import serie_excel_vers_date

NOMS_FICHIERS = {
    "source": "Employe_Source_Anonymise_VF.xlsx",
    "cible": "Employe_Destination_Anonymise_VF.xlsx",
    "detail_poste": "détail_du_poste.xlsx",
    "motifs": "Motif de la situation d'emploi.xlsx",
    "mapping": "Mapping.xlsx",
}


# Mot-clé distinctif de chaque fichier : sert à le retrouver même si l'accent du nom
# a été altéré à l'extraction du zip (ex. « détail » devenu « d├⌐tail » sous Windows).
MOTS_CLES = {"source": "source", "cible": "destination", "detail_poste": "poste",
             "motifs": "motif", "mapping": "mapping"}


def _sans_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def trouver_fichier(dossier: str | Path, cle: str) -> Path:
    """Retrouve le fichier d'extraction `cle` dans `dossier`, tolérant aux accents altérés."""
    d = Path(dossier)
    exact = d / NOMS_FICHIERS[cle]
    if exact.exists():
        return exact
    voulu = _sans_accents(NOMS_FICHIERS[cle])
    xlsx = [f for f in d.iterdir() if f.suffix.lower() == ".xlsx"]
    for f in xlsx:
        if _sans_accents(f.name) == voulu:
            return f
    candidats = [f for f in xlsx if MOTS_CLES[cle] in _sans_accents(f.name)]
    if len(candidats) == 1:
        return candidats[0]
    raise FileNotFoundError(f"Fichier « {NOMS_FICHIERS[cle]} » introuvable dans {d} "
                            f"(fichiers présents : {[f.name for f in d.iterdir()]})")


@dataclass
class Donnees:
    source: pd.DataFrame
    cible: pd.DataFrame
    detail_poste: pd.DataFrame
    motifs: pd.DataFrame
    mapping_cellules: dict[str, str]          # ex. {"Mapping!D24": "SI PERM_IND=1 ..."}
    regles_situation: pd.DataFrame
    empreintes: dict[str, str] = field(default_factory=dict)  # nom → sha256
    alertes_chargement: list[str] = field(default_factory=list)

    def texte_mapping(self, *cellules: str) -> str:
        return "\n".join(self.mapping_cellules.get(c, "") for c in cellules if self.mapping_cellules.get(c))


def sha256(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


def _lire_octets(f: str | Path | BinaryIO | bytes) -> bytes:
    if isinstance(f, bytes):
        return f
    if hasattr(f, "read"):
        data = f.read()
        if hasattr(f, "seek"):
            f.seek(0)
        return data
    with open(f, "rb") as fh:  # lecture seule
        return fh.read()


def _lire_detail_poste(octets: bytes) -> pd.DataFrame:
    """L'extraction 'détail du poste' est un CSV stocké dans une seule colonne Excel."""
    brut = pd.read_excel(io.BytesIO(octets), header=None, dtype=str)
    if brut.shape[1] == 1 and "," in str(brut.iloc[0, 0]):
        texte = "\n".join(brut[0].dropna().tolist())
        df = pd.read_csv(io.StringIO(texte), dtype=str)
    else:
        df = pd.read_excel(io.BytesIO(octets), dtype=str)
    df = df.apply(lambda c: c.str.strip() if c.dtype == object else c)
    # DateEffetAffectation est un numéro de série Excel → date réelle (N-EXCEL-SERIE)
    df["DateEffet"] = df["DateEffetAffectation"].map(serie_excel_vers_date)
    return df


def _lire_mapping(octets: bytes) -> tuple[dict[str, str], pd.DataFrame]:
    wb = openpyxl.load_workbook(io.BytesIO(octets), read_only=True, data_only=True)
    cellules: dict[str, str] = {}
    for ws in wb.worksheets:
        for ligne in ws.iter_rows():
            for c in ligne:
                if c.value is not None and hasattr(c, "coordinate"):
                    cellules[f"{ws.title}!{c.coordinate}"] = str(c.value).strip()
    situation = pd.read_excel(io.BytesIO(octets), sheet_name="Règles situation d'emploi", dtype=str)
    return cellules, situation


def charger(fichiers: dict[str, str | Path | BinaryIO | bytes] | None = None,
            dossier: str | Path | None = None) -> Donnees:
    """Charge les cinq extractions. `fichiers` permet de passer des fichiers
    téléversés (app Streamlit) ; sinon on lit `dossier`."""
    fichiers = dict(fichiers or {})
    if dossier is not None:
        for cle, nom in NOMS_FICHIERS.items():
            fichiers.setdefault(cle, trouver_fichier(dossier, cle))
    manquants = [k for k in NOMS_FICHIERS if k not in fichiers]
    if manquants:
        raise FileNotFoundError(f"Fichiers manquants : {manquants}")

    octets = {k: _lire_octets(v) for k, v in fichiers.items() if k in NOMS_FICHIERS}
    empreintes = {NOMS_FICHIERS[k]: sha256(b) for k, b in octets.items()}

    source = pd.read_excel(io.BytesIO(octets["source"]), dtype=str)
    cible = pd.read_excel(io.BytesIO(octets["cible"]), dtype=str)
    motifs = pd.read_excel(io.BytesIO(octets["motifs"]), dtype=str)
    detail = _lire_detail_poste(octets["detail_poste"])
    cellules, situation = _lire_mapping(octets["mapping"])

    for df in (source, cible, motifs):
        for c in df.columns:
            df[c] = df[c].map(lambda v: v.strip() if isinstance(v, str) else v)

    source.insert(0, "_ligne_source", range(2, len(source) + 2))   # n° de ligne Excel
    cible.insert(0, "_ligne_cible", range(2, len(cible) + 2))

    alertes = []
    if source["Matricule"].isna().any():
        alertes.append("Des lignes source n'ont pas de matricule.")
    if cible["personId"].isna().any():
        alertes.append("Des lignes cible n'ont pas de personId.")

    return Donnees(source=source, cible=cible, detail_poste=detail, motifs=motifs,
                   mapping_cellules=cellules, regles_situation=situation,
                   empreintes=empreintes, alertes_chargement=alertes)


def verifier_integrite(dossier: str | Path, empreintes: dict[str, str]) -> dict[str, bool]:
    """Recalcule les empreintes après l'analyse : True = fichier inchangé."""
    res = {}
    for cle, nom in NOMS_FICHIERS.items():
        try:
            p = trouver_fichier(dossier, cle)
        except FileNotFoundError:
            res[nom] = False
            continue
        res[nom] = nom in empreintes and sha256(p.read_bytes()) == empreintes[nom]
    return res
