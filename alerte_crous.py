"""Alerte logements CROUS : surveille trouverunlogement.lescrous.fr et prévient
dès qu'un logement correspondant aux critères se libère.

Réglages dans config.txt (même dossier). Lancement : double-clic sur Lancer.bat.
Notifications : fenêtre Windows + son, et (optionnel) message Discord via webhook.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent
SITE = "https://trouverunlogement.lescrous.fr"
UA = {"User-Agent": "Mozilla/5.0 (alerte-crous perso)"}
DEJA_VUS = DOSSIER / "deja_vus.json"

# Rectangles (nord-ouest, sud-est) couvrant chaque département ; on refiltre ensuite par code postal.
ZONES_DEPARTEMENTS = {
    "75": [{"lon": 2.22, "lat": 48.91}, {"lon": 2.47, "lat": 48.81}],
    "92": [{"lon": 2.14, "lat": 48.96}, {"lon": 2.34, "lat": 48.72}],
    "93": [{"lon": 2.28, "lat": 49.02}, {"lon": 2.61, "lat": 48.80}],
    "94": [{"lon": 2.30, "lat": 48.87}, {"lon": 2.62, "lat": 48.68}],
}


# ---------- configuration ----------
def lire_config():
    fichier = DOSSIER / "config.txt"
    if not fichier.exists():
        sys.exit("config.txt introuvable.")
    conf = {}
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#") and "=" in ligne:
            cle, valeur = ligne.split("=", 1)
            conf[cle.strip().upper()] = valeur.strip()
    def liste(cle):
        return [v.strip() for v in conf.get(cle, "").split(",") if v.strip()]

    villes, departements = liste("VILLES"), liste("DEPARTEMENTS")
    inconnus = [d for d in departements if d not in ZONES_DEPARTEMENTS]
    if inconnus:
        sys.exit(f"Département(s) non géré(s) : {inconnus}. Possibles : {list(ZONES_DEPARTEMENTS)}")
    if not villes and not departements:
        sys.exit("Indique DEPARTEMENTS=... ou VILLES=... dans config.txt.")
    return {
        "villes": villes,
        "departements": departements,
        "prioritaires": liste("CODES_POSTAUX_PRIORITAIRES"),
        "rayon_km": float(conf.get("RAYON_KM", "10") or 10),
        "loyer_max": float(conf.get("LOYER_MAX", "0") or 0),
        "surface_min": float(conf.get("SURFACE_MIN", "0") or 0),
        "intervalle": max(60, int(conf.get("INTERVALLE_SECONDES", "120") or 120)),
        # Les secrets peuvent venir des variables d'environnement (serveur en ligne) plutôt que du fichier.
        "webhook": os.environ.get("DISCORD_WEBHOOK") or conf.get("DISCORD_WEBHOOK", ""),
        "whatsapp_numero": os.environ.get("WHATSAPP_NUMERO") or conf.get("WHATSAPP_NUMERO", ""),
        "callmebot_cle": os.environ.get("CALLMEBOT_CLE") or conf.get("CALLMEBOT_CLE", ""),
    }


# ---------- accès web ----------
def http(url, data=None):
    corps = json.dumps(data).encode() if data is not None else None
    entetes = dict(UA)
    if corps:
        entetes["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=corps, headers=entetes)
    with urllib.request.urlopen(req, timeout=30) as rep:
        return json.loads(rep.read().decode("utf-8"))


def outil_en_cours():
    """Le CROUS change d'identifiant de campagne chaque année (ex. 48) : on le lit dans le contexte du site."""
    outil = http(SITE + "/api/global/context")["tools"]["currentSchoolYear"]
    return int(outil["id"]), outil["mechanism"]


def zone_ville(ville, rayon_km):
    """Coordonnées de la ville via l'API Adresse du gouvernement, puis un carré de ±rayon."""
    url = "https://api-adresse.data.gouv.fr/search/?" + urllib.parse.urlencode(
        {"q": ville, "type": "municipality", "limit": 1}
    )
    res = http(url)
    if not res.get("features"):
        sys.exit(f"Ville introuvable : {ville}")
    lon, lat = res["features"][0]["geometry"]["coordinates"]
    nom = res["features"][0]["properties"]["label"]
    d_lat = rayon_km / 111.0
    d_lon = rayon_km / (111.0 * 0.70)  # approximation valable pour la France métropolitaine
    return nom, [{"lon": lon - d_lon, "lat": lat + d_lat}, {"lon": lon + d_lon, "lat": lat - d_lat}]


def chercher(outil, mecanisme, zone):
    logements, page = [], 1
    while True:
        res = http(f"{SITE}/api/fr/search/{outil}", {
            "idTool": outil, "need_aggregation": False, "page": page, "pageSize": 100,
            "sector": None, "occupationModes": [], "location": zone, "residence": None,
            "precision": 6, "equipment": [], "price": {"max": 10000000}, "area": {"min": 0},
            "adaptedPmr": False, "toolMechanism": mecanisme,
        })["results"]
        logements.extend(res["items"])
        if len(logements) >= res["total"]["value"] or not res["items"]:
            return logements
        page += 1


# ---------- filtres et affichage ----------
def loyer_min(log):
    loyers = [m["rent"]["min"] for m in log.get("occupationModes", []) if m.get("rent")]
    return min(loyers) / 100 if loyers else None


def code_postal(log):
    trouve = re.search(r"\b(\d{5})\b", log["residence"].get("address", ""))
    return trouve.group(1) if trouve else ""


def est_prioritaire(log, conf):
    return code_postal(log) in conf["prioritaires"]


def correspond(log, conf):
    if conf["departements"] and not conf["villes"]:
        if code_postal(log)[:2] not in conf["departements"]:
            return False
    loyer = loyer_min(log)
    if conf["loyer_max"] and loyer is not None and loyer > conf["loyer_max"]:
        return False
    if conf["surface_min"] and (log.get("area") or {}).get("min", 0) < conf["surface_min"]:
        return False
    return True


def resume(log, outil):
    loyer = loyer_min(log)
    surface = (log.get("area") or {}).get("min")
    return {
        "titre": f"{log['label']} - {log['residence']['label']}",
        "adresse": log["residence"].get("address", ""),
        "loyer": f"{loyer:.2f} €" if loyer is not None else "?",
        "surface": f"{surface:g} m²" if surface else "?",
        "lien": f"{SITE}/tools/{outil}/accommodations/{log['id']}",
    }


# ---------- notifications ----------
def notif_windows(titre, texte):
    titre = titre.replace("'", "''")
    texte = texte.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Media.SystemSounds]::Exclamation.Play();"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
        f"$n.ShowBalloonTip(15000,'{titre}','{texte}',[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Seconds 16;$n.Dispose()"
    )
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def notif_discord(webhook, r):
    if not webhook:
        return
    msg = {"embeds": [{
        "title": "🏠 " + r["titre"], "url": r["lien"], "color": 0x2ECC71,
        "fields": [
            {"name": "Loyer", "value": r["loyer"], "inline": True},
            {"name": "Surface", "value": r["surface"], "inline": True},
            {"name": "Adresse", "value": r["adresse"] or "?"},
        ],
    }]}
    try:
        http(webhook, msg)
    except urllib.error.HTTPError as e:
        if e.code not in (200, 204):
            print(f"  (webhook Discord refusé : {e.code})")
    except Exception as e:  # le webhook renvoie souvent un corps vide -> pas grave
        if "Expecting value" not in str(e):
            print(f"  (webhook Discord : {e})")


# ---------- boucle principale ----------
def notif_whatsapp(conf, texte):
    """WhatsApp gratuit via CallMeBot (clé obtenue en envoyant un message d'activation à leur bot)."""
    if not (conf["whatsapp_numero"] and conf["callmebot_cle"]):
        return
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": conf["whatsapp_numero"], "text": texte, "apikey": conf["callmebot_cle"]})
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
    except Exception as e:
        print(f"  (WhatsApp : {e})")


def tour(conf, outil, mecanisme, zones, deja_vus, premier_tour):
    heure = datetime.now().strftime("%H:%M:%S")
    trouves = {}
    for _, zone in zones:
        for log in chercher(outil, mecanisme, zone):
            if correspond(log, conf):
                trouves[str(log["id"])] = log
    nouveaux = [l for i, l in trouves.items() if i not in deja_vus]
    nouveaux.sort(key=lambda l: not est_prioritaire(l, conf))  # prioritaires en premier

    if premier_tour:
        print(f"[{heure}] {len(trouves)} logement(s) déjà disponible(s) au démarrage :")
    for log in nouveaux:
        r = resume(log, outil)
        if est_prioritaire(log, conf):
            r["titre"] = "⭐ PRIORITAIRE ⭐ " + r["titre"]
        print(f"[{heure}] NOUVEAU : {r['titre']} | {r['loyer']} | {r['surface']}"
              f" | {r['adresse']}\n          {r['lien']}")
        notif_discord(conf["webhook"], r)
        notif_whatsapp(conf, f"🏠 CROUS dispo : {r['titre']}\n{r['loyer']} - {r['surface']}\n"
                             f"{r['adresse']}\n{r['lien']}")
    if nouveaux and sys.platform == "win32":
        premier = resume(nouveaux[0], outil)["titre"]
        titre = ("⭐ Vanves/Malakoff DISPO !" if est_prioritaire(nouveaux[0], conf)
                 else "Logement CROUS dispo !")
        notif_windows(titre, f"{len(nouveaux)} nouveau(x) : {premier}")
    elif not nouveaux and not premier_tour:
        print(f"[{heure}] rien de nouveau ({len(trouves)} dispo correspondant(s))")

    # On garde seulement ce qui est dispo maintenant : un logement qui repart puis revient re-déclenche l'alerte.
    DEJA_VUS.write_text(json.dumps(sorted(trouves)))
    return set(trouves)


def main():
    une_fois = "--une-fois" in sys.argv  # mode serveur en ligne (lancé toutes les 5 min)
    conf = lire_config()
    outil, mecanisme = outil_en_cours()
    zones = [zone_ville(v, conf["rayon_km"]) for v in conf["villes"]]
    zones += [(f"département {d}", ZONES_DEPARTEMENTS[d]) for d in conf["departements"]]
    deja_vus = set(json.loads(DEJA_VUS.read_text())) if DEJA_VUS.exists() else set()
    premier_tour = not DEJA_VUS.exists()

    print(f"Campagne CROUS n°{outil}. Surveillance de : {', '.join(n for n, _ in zones)}"
          f" (loyer max {conf['loyer_max'] or 'aucun'} €)")
    if conf["prioritaires"]:
        print(f"Codes postaux prioritaires : {', '.join(conf['prioritaires'])}")
    if une_fois:
        tour(conf, outil, mecanisme, zones, deja_vus, premier_tour)
        return
    print(f"Vérification toutes les {conf['intervalle']} s. Laisse cette fenêtre ouverte.\n")

    while True:
        try:
            deja_vus = tour(conf, outil, mecanisme, zones, deja_vus, premier_tour)
            premier_tour = False
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] erreur réseau, nouvel essai au prochain tour : {e}")
        time.sleep(conf["intervalle"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt.")
