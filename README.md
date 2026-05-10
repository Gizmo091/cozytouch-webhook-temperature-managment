# Cozytouch local app

Pilotage local des équipements **Atlantic Cozytouch** (radiateurs, thermostats…) via une UI web,
des **presets** et des **webhooks** publics. Sous le capot : Python + [`pyoverkiz`](https://github.com/iMicknl/python-overkiz-api),
empaqueté en Docker.

L'app sert :
- une **UI** sur `/` (sélection par pièce, raccourcis 19°/12°/hors gel, gestion des presets)
- des **webhooks publics** sur `/webhooks/{token}/run` (URL avec secret intégré → utilisables
  depuis Home Assistant, iOS Shortcut, Shelly, IFTTT, un cron…)
- une **API HTTP** que l'UI consomme et que tu peux taper en ligne de commande si besoin
  (Swagger auto-généré sur `/docs`)

> **Pas d'auth Bearer.** Le service est lié à `127.0.0.1` par défaut et conçu pour un usage
> local. Si tu l'exposes au-delà de ta machine, mets une protection devant
> (reverse proxy + HTTP basic auth, VPN…). Voir la section **Exposer hors localhost** plus bas.

## Démarrage rapide

```bash
cp .env.example .env
# édite .env avec tes identifiants Atlantic Cozytouch
docker compose up -d --build
```

```bash
curl http://localhost:8000/health
# → {"status":"ok","server":"atlantic_cozytouch"}
```

### Utiliser l'image Docker pré-build (GHCR)

Une image multi-arch (amd64 + arm64) est publiée par CI à chaque push sur `main` et à
chaque tag `v*` :

```bash
docker pull ghcr.io/gizmo091/cozytouch-webhook-temperature-managment:latest
```

Pour t'en servir au lieu de builder localement, dans `docker-compose.yml` remplace
`build: .` par :

```yaml
image: ghcr.io/gizmo091/cozytouch-webhook-temperature-managment:latest
```

UI : <http://localhost:8000/>  ·  Swagger : <http://localhost:8000/docs>

## Configuration

| Variable             | Obligatoire | Description                                                         |
| -------------------- | ----------- | ------------------------------------------------------------------- |
| `COZYTOUCH_USERNAME` | oui         | Email du compte Atlantic Cozytouch                                  |
| `COZYTOUCH_PASSWORD` | oui         | Mot de passe du compte                                              |
| `COZYTOUCH_SERVER`   | non         | Défaut `atlantic_cozytouch`. Voir liste dans `.env.example`         |
| `COZYTOUCH_TOKEN`    | non         | JWT pré-généré, fallback si l'auth login/password est rejetée       |
| `PRESETS_FILE`       | non         | Chemin du JSON de stockage des presets. Compose force `/data/presets.json` |
| `LOG_LEVEL`          | non         | `INFO` par défaut                                                   |
| `PORT`               | non         | `8000` par défaut                                                   |

## Routes HTTP

Toutes accessibles sans auth depuis localhost. Les routes `webhooks/{token}/run` ont leur propre
secret dans l'URL (token aléatoire généré par preset).

| Méthode | Chemin                      | Description                                    |
| ------- | --------------------------- | ---------------------------------------------- |
| GET     | `/health`                   | Ping                                           |
| GET     | `/devices`                  | Liste brute des équipements (sous-devices `#2..#5` inclus) |
| GET     | `/devices/grouped`          | Vue fusionnée : un radiateur = ses 4 sensors merged in (mesure, fenêtre, présence, conso) + `place_name` (la pièce) + `category` |
| GET     | `/places`                   | Liste des pièces `[{oid, name, device_urls}]` |
| GET     | `/devices/state`            | État courant d'un device (param `device_url`)  |
| POST    | `/devices/refresh`          | Rafraîchit tous les états                      |
| POST    | `/devices/refresh/single`   | Rafraîchit un seul équipement                  |
| POST    | `/devices/commands`         | Envoie une commande                            |
| POST    | `/devices/commands/batch`   | Envoie plusieurs commandes en une fois         |
| GET     | `/setup`                    | Dump complet (debug)                           |
| GET     | `/presets`                  | Liste les presets                              |
| POST    | `/presets`                  | Crée un preset                                 |
| GET     | `/presets/{id}`             | Lit un preset                                  |
| PATCH   | `/presets/{id}`             | Modifie un preset                              |
| DELETE  | `/presets/{id}`             | Supprime un preset                             |
| GET/POST | `/presets/{id}/run`        | Exécute un preset                              |
| POST    | `/presets/{id}/rotate-webhook` | Régénère le token webhook                   |
| GET/POST | `/webhooks/{token}/run`    | **Public** : exécute le preset via son token URL. GET pour signets / Shortcut iOS / IFTTT |

### Exemples

```bash
BASE="http://localhost:8000"

# Lister les pièces et leurs radiateurs
curl "$BASE/places" | jq

# Régler la consigne à 19°C sur 3 radiateurs d'un coup
curl -H "Content-Type: application/json" \
     -d '{"actions":[
           {"device_url":"io://X/1","command":"setTargetTemperature","parameters":[19.0]},
           {"device_url":"io://X/2","command":"setTargetTemperature","parameters":[19.0]},
           {"device_url":"io://X/3","command":"setTargetTemperature","parameters":[19.0]}
         ]}' \
     "$BASE/devices/commands/batch"

# Créer un preset « Salle à Manger 19° »
curl -H "Content-Type: application/json" \
     -d '{"name":"SAM 19°","actions":[
           {"device_url":"io://X/1","command":"setTargetTemperature","parameters":[19.0]},
           {"device_url":"io://X/2","command":"setTargetTemperature","parameters":[19.0]}
         ]}' \
     "$BASE/presets"
# → { "id":"…", "webhook_token":"…", … }

# Le déclencher depuis n'importe où (juste un GET ou POST sur l'URL)
curl "$BASE/webhooks/<webhook_token>/run"
```

## Interface web

Sur <http://localhost:8000/>. Toggle **« regrouper par pièce »** activé par défaut : les
radiateurs de la même pièce (au sens de l'app Atlantic) apparaissent comme un seul item.
Cocher → applique la commande aux N radiateurs sous-jacents.

Pour pérenniser une combinaison sélection + action, tape un nom dans le champ « Sauver l'action
courante en preset ». Le preset apparaît dans le panneau du bas avec son URL webhook copiable.

### Commandes courantes

Le nom et les paramètres dépendent du `controllable_name` de chaque équipement.
Quelques classiques observés sur les radiateurs Atlantic :

- `setTargetTemperature` — `[float]` consigne en °C
- `setHeatingLevel` — `["off"|"comfort"|"eco"|"frostprotection"]`
- `setOperatingMode` — `["normal"|"eco"|"frostprotection"|…]`

`GET /setup` retourne la liste exhaustive des commandes disponibles par équipement (champ
`definition.commands`).

## Exposer hors localhost

Par défaut le port est lié à `127.0.0.1` dans `docker-compose.yml`. Pour rendre l'app
accessible depuis le LAN ou Internet, **ne fais pas que retirer le `127.0.0.1:`** — l'app n'a
pas d'auth, n'importe qui sur le réseau pourrait piloter tes radiateurs et créer/supprimer
des webhooks.

Plutôt :

- **Reverse proxy + HTTP basic auth** (Caddy, nginx, Traefik) devant
- **VPN** (Tailscale, WireGuard) → tu accèdes à l'app comme si tu étais en local
- Ou **expose uniquement les webhooks** (`/webhooks/*`) en bloquant le reste — le secret est dans l'URL, c'est ok à exposer

## Développement local (sans Docker)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # édite-le
uvicorn app.main:app --reload --port 8000
```

## Troubleshooting

- **502 « credentials rejected »** → identifiants Cozytouch faux, ou compte verrouillé après
  plusieurs essais (Atlantic peut imposer un cooldown).
- **502 « session lost »** → le JWT a expiré et n'a pas pu se renouveler. Tente de définir
  manuellement `COZYTOUCH_TOKEN` (sniffé via l'app mobile + mitmproxy si nécessaire).
- **429 rate-limit** → l'API Overkiz limite agressivement. Évite de poller à < 30 s.
- **Liste de devices vide** → le serveur configuré ne correspond pas à ton compte. Atlantic
  Cozytouch utilise `atlantic_cozytouch`, mais certains comptes Thermor/Sauter sont sur un
  autre endpoint (`thermor_cozytouch` etc.).
- **Une pièce est absente** → vérifie que tu as bien rangé les radiateurs dans des pièces
  côté app Atlantic. Clique « Recharger » dans l'UI (force `?refresh=true` qui re-fetche le
  setup, donc l'arbre des pièces).

## Architecture

```
[Navigateur, curl, iOS Shortcut, HA…]
              │
              ▼ HTTP
       [FastAPI app/main.py]
              │
              ▼  in-process call
        [app/client.py]  ── singleton + auto-reconnect
              │
              ▼ pyoverkiz (async)
              ▼
   [Cloud Atlantic Cozytouch / Overkiz]
              │
              ▼
       [Radiateurs / box]
```

Le client pyoverkiz est instancié une seule fois en mémoire. Si une requête échoue avec
`NotAuthenticatedException` (JWT expiré côté Overkiz), un nouveau login est tenté
automatiquement et l'appel est rejoué une fois.

## Licence et provenance

Ce projet s'appuie sur [`pyoverkiz`](https://github.com/iMicknl/python-overkiz-api) (licence MIT)
qui est lui-même utilisé en interne par l'intégration Cozytouch officielle de Home Assistant.
Il ne contient aucun extrait du code de l'application Atlantic.
