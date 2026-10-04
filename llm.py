"""Fournisseurs LLM interchangeables avec sortie JSON structurée et cache.

- anthropic : API Claude (variable ANTHROPIC_API_KEY ; modèle via CORROBORAI_MODELE)
- ollama    : modèle local (aucune donnée ne quitte le poste) via OLLAMA_HOST
- aucun     : l'arbitre local hors-ligne est utilisé

Les réponses sont mises en cache (hash du prompt) dans .cache_llm/ pour
rendre les exécutions reproductibles et éviter des appels répétés.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

MODELE_ANTHROPIC_DEFAUT = "claude-sonnet-5-5"
MODELE_OLLAMA_DEFAUT = "llama3.1"
DOSSIER_CACHE = Path(os.environ.get("CORROBORAI_CACHE", ".cache_llm"))


class ErreurLLM(RuntimeError):
    pass


class FournisseurLLM:
    nom = "abstrait"
    modele = ""

    def json(self, systeme: str, utilisateur: str, schema: dict[str, Any]) -> dict[str, Any]:
        cle = hashlib.sha256(json.dumps([self.nom, self.modele, systeme, utilisateur, schema],
                                        ensure_ascii=False).encode()).hexdigest()[:24]
        f = DOSSIER_CACHE / f"{cle}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        rep = self._appel(systeme, utilisateur, schema)
        try:
            DOSSIER_CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        return rep

    def _appel(self, systeme: str, utilisateur: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class Anthropic(FournisseurLLM):
    nom = "anthropic"

    def __init__(self, modele: str | None = None, client: Any = None):
        self.modele = modele or os.environ.get("CORROBORAI_MODELE", MODELE_ANTHROPIC_DEFAUT)
        if client is None:
            import anthropic  # import paresseux
            client = anthropic.Anthropic()
        self.client = client

    def _appel(self, systeme, utilisateur, schema):
        # Sortie structurée fiable : on force l'appel d'un « outil » dont le schéma est la réponse attendue
        rep = self.client.messages.create(
            model=self.modele, max_tokens=4000, temperature=0, system=systeme,
            tools=[{"name": "rendre_verdicts", "description": "Retourne les verdicts structurés.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": "rendre_verdicts"},
            messages=[{"role": "user", "content": utilisateur}],
        )
        for bloc in rep.content:
            if getattr(bloc, "type", "") == "tool_use":
                return dict(bloc.input)
        raise ErreurLLM("Réponse sans sortie structurée")


class Ollama(FournisseurLLM):
    nom = "ollama"

    def __init__(self, modele: str | None = None):
        self.modele = modele or os.environ.get("CORROBORAI_MODELE", MODELE_OLLAMA_DEFAUT)
        self.hote = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    def _appel(self, systeme, utilisateur, schema):
        import requests
        r = requests.post(f"{self.hote}/api/chat", timeout=300, json={
            "model": self.modele, "stream": False, "format": schema, "options": {"temperature": 0},
            "messages": [{"role": "system", "content": systeme}, {"role": "user", "content": utilisateur}]})
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])


def creer(fournisseur: str = "auto", modele: str | None = None) -> FournisseurLLM | None:
    """Retourne un fournisseur utilisable, ou None (→ arbitre local)."""
    f = (fournisseur or "auto").lower()
    if f == "aucun":
        return None
    if f in ("anthropic", "auto") and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return Anthropic(modele)
        except Exception:  # SDK absent, etc.
            if f == "anthropic":
                raise
    if f == "ollama" or (f == "auto" and os.environ.get("OLLAMA_HOST")):
        return Ollama(modele)
    if f == "anthropic":
        raise ErreurLLM("ANTHROPIC_API_KEY non définie")
    return None
