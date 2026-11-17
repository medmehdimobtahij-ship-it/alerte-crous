"""Connexion à trouverunlogement.lescrous.fr : TU te connectes dans la fenêtre qui s'ouvre
(identifiants MesServices.etudiant.gouv.fr). Le script ne voit ni ne stocke ton mot de passe :
il garde seulement le cookie de session dans session.json (ne partage jamais ce fichier).

Il enregistre aussi diagnostic.json (panier, pièces demandées, demandes en cours) pour
préparer l'envoi automatique des demandes.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DOSSIER = Path(__file__).resolve().parent
SITE = "https://trouverunlogement.lescrous.fr"
SESSION = DOSSIER / "session.json"
DIAGNOSTIC = DOSSIER / "diagnostic.json"


def api(context, chemin):
    rep = context.request.get(f"{SITE}/api/fr{chemin}",
                              headers={"Accept": "application/ld+json, application/json"})
    try:
        return rep.status, rep.json()
    except Exception:
        return rep.status, rep.text()[:300]


def main():
    with sync_playwright() as p:
        try:
            navigateur = p.chromium.launch(channel="msedge", headless=False)
        except Exception:
            navigateur = p.chromium.launch(channel="chrome", headless=False)
        context = navigateur.new_context()
        page = context.new_page()
        page.goto(f"{SITE}/mse/discovery/connect")

        print("Connecte-toi dans la fenêtre du navigateur (MesServices.etudiant.gouv.fr).")
        print("J'attends que la connexion soit faite (10 minutes max)...")
        utilisateur = None
        for _ in range(200):
            time.sleep(3)
            try:
                statut, utilisateur = api(context, "/user")
            except Exception:
                continue
            if statut == 200 and isinstance(utilisateur, dict):
                break
            utilisateur = None
        if not utilisateur:
            navigateur.close()
            sys.exit("Connexion non détectée. Relance le script.")

        context.storage_state(path=str(SESSION))
        print(f"\nConnecté : {utilisateur.get('firstName', '')} {utilisateur.get('lastName', '')}".rstrip())

        contexte = context.request.get(f"{SITE}/api/global/context").json()
        outil = contexte["tools"]["currentSchoolYear"]["id"]
        diag = {"outil": outil, "utilisateur_champs": sorted(utilisateur)}
        for nom, chemin in [("panier", f"/tools/{outil}/cart"),
                            ("pieces_justificatives", "/attachments"),
                            ("demandes", f"/tools/{outil}/requests")]:
            statut, contenu = api(context, chemin)
            diag[nom] = {"statut_http": statut, "contenu": contenu}
        DIAGNOSTIC.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Session enregistrée (session.json) et diagnostic.json créé.")
        print("Tu peux fermer cette fenêtre et dire à Claude que c'est fait.")
        navigateur.close()


if __name__ == "__main__":
    main()
