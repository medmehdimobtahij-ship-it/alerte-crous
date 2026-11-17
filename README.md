# Alerte CROUS

Surveille les logements libérés sur [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr)
(Paris et Hauts-de-Seine par défaut) et envoie une alerte WhatsApp / Discord / Windows.

- `config.txt` : départements, codes postaux prioritaires, loyer max…
- En ligne : GitHub Actions lance `python alerte_crous.py --une-fois` toutes les 5 minutes.
  Secrets à définir : `WHATSAPP_NUMERO` (+33…), `CALLMEBOT_CLE` (clé CallMeBot), `DISCORD_WEBHOOK` (optionnel).
- Sur PC : `Lancer.bat` (vérification toutes les 2 minutes).
