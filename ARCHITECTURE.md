# Architecture Technique — Billets & Vinted Monitor MVP

**Date :** 2026-02-25
**Stack :** Python Flask / SQLite / Google OAuth 2.0 / Gmail API / Sheets API / Drive API
**Deploiement :** VPS Hostinger (Gunicorn + Nginx + cron)

---

## Schema d'architecture

```
+--------------------------------------------------+
|                    INTERNET                       |
+--------------------------------------------------+
            |                          |
            v                          v
+---------------------+    +---------------------+
|   Google OAuth 2.0  |    |   Google APIs        |
|   (accounts.google) |    |   - Gmail API        |
|                     |    |   - Sheets API       |
|   Scopes:           |    |   - Drive API        |
|   - gmail.readonly  |    |   - UserInfo API     |
|   - spreadsheets    |    |                      |
|   - drive.file      |    |                      |
|   - openid          |    |                      |
|   - userinfo.email  |    |                      |
|   - userinfo.profile|    |                      |
+---------------------+    +---------------------+
            |                          ^
            v                          |
+--------------------------------------------------+
|                  NGINX (reverse proxy)            |
|  - SSL termination (Let's Encrypt)               |
|  - Sert /static/ directement                     |
|  - Proxy vers Gunicorn :5000                     |
+--------------------------------------------------+
            |
            v
+--------------------------------------------------+
|             GUNICORN (WSGI server)                |
|  - 3 workers                                     |
|  - bind 127.0.0.1:5000                           |
|  - timeout 120s                                  |
+--------------------------------------------------+
            |
            v
+--------------------------------------------------+
|               FLASK APPLICATION                   |
|                                                   |
|  +--------------------------------------------+  |
|  |  Routes Web                                 |  |
|  |  GET  /            -> landing page          |  |
|  |  GET  /auth/google -> initier OAuth         |  |
|  |  GET  /oauth/callback -> recevoir token     |  |
|  |  GET  /oauth/add-gmail -> OAuth 2e compte   |  |
|  |  GET  /dashboard   -> espace client         |  |
|  |  GET  /logout      -> deconnexion           |  |
|  +--------------------------------------------+  |
|                                                   |
|  +--------------------------------------------+  |
|  |  Routes API (JSON)                          |  |
|  |  POST /api/create-sheet                     |  |
|  |  POST /api/link-sheet                       |  |
|  |  POST /api/scan-now                         |  |
|  |  GET  /api/scan-logs                        |  |
|  |  GET  /api/me                               |  |
|  |  GET  /api/gmail-accounts                   |  |
|  |  GET  /api/spreadsheet                      |  |
|  |  GET  /api/stats                            |  |
|  |  POST /api/add-gmail                        |  |
|  |  DELETE /api/gmail-accounts/<id>            |  |
|  +--------------------------------------------+  |
|                                                   |
|  +--------------------------------------------+  |
|  |  Modules internes                           |  |
|  |  - oauth_utils.py (token management)        |  |
|  |  - sheets.py (CRUD Google Sheets)           |  |
|  |  - parsers/tickets.py (TM, RG, SDF)        |  |
|  |  - parsers/vinted.py (transactions)         |  |
|  |  - parsers/email_utils.py (extraction)      |  |
|  |  - db.py (SQLite CRUD)                      |  |
|  +--------------------------------------------+  |
|                                                   |
+--------------------------------------------------+
            |
            v
+--------------------------------------------------+
|               SQLite DATABASE                     |
|  data/monitor.db                                  |
|                                                   |
|  Tables:                                          |
|  - users                                          |
|  - gmail_accounts                                 |
|  - spreadsheets                                   |
|  - scan_logs                                      |
|  - processed_orders                               |
+--------------------------------------------------+

+--------------------------------------------------+
|               CRON JOB (systemd timer)            |
|  Toutes les 15 minutes:                           |
|  python3 /opt/billets-monitor/backend/scanner.py  |
|                                                   |
|  Workflow:                                        |
|  1. Charger tous les users actifs                 |
|  2. Pour chaque user:                             |
|     a. Refresh token si necessaire                |
|     b. Scanner chaque gmail_account               |
|     c. Parser les emails selon monitoring_type    |
|     d. Deduplication via processed_orders         |
|     e. Ecrire dans le Google Sheet                |
|     f. Logger dans scan_logs                      |
+--------------------------------------------------+
```

---

## Schema de la base SQLite

```
+------------------+       +--------------------+
|     users        |       |  gmail_accounts    |
+------------------+       +--------------------+
| id (PK)          |<---+  | id (PK)            |
| email            |    |  | user_id (FK)  -----+
| name             |    |  | email              |
| picture          |    |  | oauth_token        |
| monitoring_type  |    |  | oauth_refresh_token|
| is_active        |    |  | token_expiry       |
| created_at       |    |  | is_primary         |
| updated_at       |    |  | created_at         |
+------------------+    |  +--------------------+
        |               |
        |               |  +--------------------+
        |               |  |   spreadsheets     |
        |               |  +--------------------+
        |               +--| user_id (FK)       |
        |                  | id (PK)            |
        |                  | spreadsheet_id     |
        |                  | spreadsheet_url    |
        |                  | is_auto_created    |
        |                  | created_at         |
        |                  +--------------------+
        |
        |  +--------------------+     +---------------------+
        |  |    scan_logs       |     |  processed_orders   |
        |  +--------------------+     +---------------------+
        +--| user_id (FK)      |  +--| user_id (FK)        |
           | id (PK)           |  |  | id (PK)             |
           | gmail_account_id  |  |  | order_number        |
           | scan_type         |  |  | source              |
           | orders_found      |  |  | email_id            |
           | status            |  |  | processed_at        |
           | error_message     |  |  +---------------------+
           | scanned_at        |
           +--------------------+
```

### Relations

- `users` 1--N `gmail_accounts` (un user peut avoir plusieurs comptes Gmail)
- `users` 1--N `spreadsheets` (un user peut avoir plusieurs Sheets, mais un seul actif)
- `users` 1--N `scan_logs` (historique des scans)
- `users` 1--N `processed_orders` (commandes deja traitees, pour deduplication)
- `gmail_accounts` 1--N `scan_logs` (chaque scan est lie a un compte Gmail)

---

## Endpoints API Flask

### Routes Web (rendu HTML)

| Methode | Route | Description | Auth |
|---------|-------|-------------|------|
| `GET` | `/` | Landing page publique. Presentation du service, 2 sections (Tickets + Vinted), CTA "Commencer" | Non |
| `GET` | `/auth/google` | Initie le flow OAuth. Accepte `?type=tickets` ou `?type=vinted` en query param. Redirige vers Google | Non |
| `GET` | `/oauth/callback` | Callback OAuth. Echange le code contre un token. Cree le user + gmail_account + Sheet. Redirige vers /dashboard | Non |
| `GET` | `/oauth/add-gmail` | Initie un flow OAuth pour ajouter un compte Gmail supplementaire. Le state encode le user_id | Oui |
| `GET` | `/oauth/add-gmail/callback` | Callback OAuth pour l'ajout d'un compte Gmail. Enregistre le nouveau compte en DB | Oui |
| `GET` | `/dashboard` | Espace client. Affiche stats, comptes, Sheet, logs. Charge les donnees via les routes API | Oui |
| `GET` | `/logout` | Vide la session Flask. Redirige vers `/` | Oui |

### Routes API (JSON)

| Methode | Route | Description | Auth | Request Body | Response |
|---------|-------|-------------|------|-------------|----------|
| `GET` | `/api/me` | Profil de l'utilisateur connecte | Oui | - | `{email, name, picture, monitoring_type, is_active, created_at}` |
| `GET` | `/api/gmail-accounts` | Liste des comptes Gmail connectes | Oui | - | `{accounts: [{id, email, is_primary, created_at}]}` |
| `POST` | `/api/create-sheet` | Cree un Google Sheet dans le Drive du client | Oui | - | `{success, spreadsheet_id, spreadsheet_url}` |
| `POST` | `/api/link-sheet` | Lie un Sheet existant (coller URL) | Oui | `{spreadsheet_url}` | `{success, spreadsheet_id, spreadsheet_url}` |
| `GET` | `/api/spreadsheet` | Info du Sheet actif | Oui | - | `{spreadsheet_id, spreadsheet_url, is_auto_created, created_at}` |
| `POST` | `/api/scan-now` | Lance un scan immediat | Oui | - | `{success, orders_found, message}` |
| `GET` | `/api/scan-logs` | 20 derniers scan logs | Oui | - | `{logs: [{id, gmail_email, scan_type, orders_found, status, error_message, scanned_at}]}` |
| `GET` | `/api/stats` | Statistiques globales du client | Oui | - | `{total_orders, gmail_accounts_count, last_scan_at, monitoring_type}` |
| `DELETE` | `/api/gmail-accounts/<id>` | Supprime un compte Gmail secondaire | Oui | - | `{success}` |

### Codes de reponse

| Code | Usage |
|------|-------|
| `200` | Succes (GET, POST avec resultat) |
| `201` | Ressource creee (POST create-sheet) |
| `400` | Requete invalide (URL de Sheet malformee, champs manquants) |
| `401` | Non authentifie (session expiree, pas de cookie) |
| `403` | Acces refuse (tenter de supprimer un compte primaire) |
| `404` | Ressource introuvable |
| `500` | Erreur serveur (erreur Google API, DB) |

### Format de reponse standard

```json
// Succes
{"success": true, "data": { ... }}

// Erreur
{"success": false, "error": "Message explicite"}
```

---

## Flow OAuth detaille

### Flow 1 : Inscription initiale (premiere connexion)

```
Client (navigateur)              Serveur Flask                 Google OAuth
       |                              |                              |
       | 1. GET /auth/google?type=tickets                            |
       |----------------------------->|                               |
       |                              |                               |
       |                              | 2. Genere state (token CSRF)  |
       |                              |    Stocke state + type en     |
       |                              |    session Flask               |
       |                              |                               |
       |                              | 3. Build authorization_url    |
       |                              |    scopes: openid,            |
       |                              |    userinfo.email,            |
       |                              |    userinfo.profile,          |
       |                              |    gmail.readonly,            |
       |                              |    spreadsheets,              |
       |                              |    drive.file                 |
       |                              |    access_type: offline       |
       |                              |    prompt: consent            |
       |                              |                               |
       | 4. 302 Redirect              |                               |
       |<-----------------------------|                               |
       |                                                              |
       | 5. GET accounts.google.com/o/oauth2/auth?...                 |
       |------------------------------------------------------------->|
       |                                                              |
       |                              [Ecran de consentement Google]   |
       |                              [Client autorise les scopes]    |
       |                                                              |
       | 6. 302 Redirect /oauth/callback?code=AUTH_CODE&state=STATE   |
       |<-------------------------------------------------------------|
       |                                                              |
       | 7. GET /oauth/callback?code=AUTH_CODE&state=STATE            |
       |----------------------------->|                               |
       |                              |                               |
       |                              | 8. Verifie state == session   |
       |                              |                               |
       |                              | 9. flow.fetch_token(code)     |
       |                              |----------------------------->  |
       |                              |                               |
       |                              | 10. Recoit: access_token,     |
       |                              |     refresh_token, expiry     |
       |                              |<-----------------------------  |
       |                              |                               |
       |                              | 11. GET userinfo (email,      |
       |                              |     name, picture)            |
       |                              |----------------------------->  |
       |                              |<-----------------------------  |
       |                              |                               |
       |                              | 12. INSERT users (email,      |
       |                              |     name, picture,            |
       |                              |     monitoring_type)          |
       |                              |                               |
       |                              | 13. INSERT gmail_accounts     |
       |                              |     (user_id, email,          |
       |                              |     oauth_token,              |
       |                              |     oauth_refresh_token,      |
       |                              |     token_expiry,             |
       |                              |     is_primary=1)             |
       |                              |                               |
       |                              | 14. Sheets API: CREATE        |
       |                              |     spreadsheet dans le Drive |
       |                              |     du client (utilise ses    |
       |                              |     propres credentials)      |
       |                              |----------------------------->  |
       |                              |<-----------------------------  |
       |                              |                               |
       |                              | 15. INSERT spreadsheets       |
       |                              |     (user_id, spreadsheet_id, |
       |                              |     spreadsheet_url,          |
       |                              |     is_auto_created=1)        |
       |                              |                               |
       |                              | 16. session['user_id'] = id   |
       |                              |                               |
       | 17. 302 Redirect /dashboard  |                               |
       |<-----------------------------|                               |
       |                                                              |
```

### Flow 2 : Ajout d'un compte Gmail supplementaire

```
Client (dashboard)               Serveur Flask                 Google OAuth
       |                              |                              |
       | 1. GET /oauth/add-gmail                                     |
       |----------------------------->|                               |
       |                              |                               |
       |                              | 2. Verifie session           |
       |                              |    (login_required)          |
       |                              |                               |
       |                              | 3. Build authorization_url   |
       |                              |    state = user_id encodé    |
       |                              |    scope: gmail.readonly     |
       |                              |    (seulement Gmail, pas     |
       |                              |    Sheets/Drive)             |
       |                              |                               |
       | 4. 302 Redirect              |                               |
       |<-----------------------------|                               |
       |                                                              |
       |        [Client se connecte avec son 2e Gmail]                |
       |                                                              |
       | 5. GET /oauth/add-gmail/callback?code=CODE&state=STATE      |
       |----------------------------->|                               |
       |                              |                               |
       |                              | 6. Decode state -> user_id   |
       |                              | 7. Echange code -> token     |
       |                              | 8. GET userinfo -> email     |
       |                              |                               |
       |                              | 9. Verifie que le compte     |
       |                              |    n'est pas deja lie        |
       |                              |                               |
       |                              | 10. INSERT gmail_accounts    |
       |                              |     (user_id, email, token,  |
       |                              |     refresh_token,           |
       |                              |     is_primary=0)            |
       |                              |                               |
       | 11. 302 Redirect /dashboard  |                               |
       |<-----------------------------|                               |
```

### Flow 3 : Scan automatique (cron)

```
Cron (scanner.py)                Serveur Flask (DB)           Google APIs
       |                              |                              |
       | 1. SELECT * FROM users       |                              |
       |    WHERE is_active = 1       |                              |
       |----------------------------->|                               |
       |<-----------------------------|                               |
       |                                                              |
       | [Pour chaque user:]                                          |
       |                                                              |
       | 2. SELECT * FROM             |                              |
       |    gmail_accounts            |                              |
       |    WHERE user_id = X         |                              |
       |----------------------------->|                               |
       |<-----------------------------|                               |
       |                                                              |
       | [Pour chaque gmail_account:]                                 |
       |                                                              |
       | 3. Charger credentials       |                              |
       |    (token, refresh_token)    |                              |
       |                              |                               |
       | 4. Si token expire:          |                              |
       |    Refresh via Google        |-----------------------------> |
       |    Mettre a jour en DB       |<-----------------------------|
       |----------------------------->|                               |
       |                              |                               |
       | 5. Gmail API: messages.list  |                              |
       |    (queries selon type)      |-----------------------------> |
       |                              |<-----------------------------|
       |                              |                               |
       | 6. Gmail API: messages.get   |                              |
       |    (pour chaque message)     |-----------------------------> |
       |                              |<-----------------------------|
       |                              |                               |
       | 7. Parser l'email            |                              |
       |    (tickets ou vinted)       |                              |
       |                              |                               |
       | 8. SELECT FROM               |                              |
       |    processed_orders          |                              |
       |    WHERE order_number = X    |                              |
       |----------------------------->|                               |
       |                              |                               |
       | 9. Si nouveau:               |                              |
       |    INSERT processed_orders   |                              |
       |----------------------------->|                               |
       |                              |                               |
       | 10. Sheets API: append row   |                              |
       |     dans le Sheet du client  |-----------------------------> |
       |                              |<-----------------------------|
       |                              |                               |
       | 11. INSERT scan_logs         |                              |
       |     (user, account, type,    |                              |
       |     orders_found, status)    |                              |
       |----------------------------->|                               |
```

---

## Structure du projet

```
billets-monitor-mvp/
|
|-- backend/
|   |-- app.py                    # Application Flask principale
|   |                              # Routes web + routes API
|   |                              # OAuth flow (inscription + ajout Gmail)
|   |                              # Session management
|   |                              # login_required decorator
|   |
|   |-- config.py                 # Configuration centralisee
|   |                              # SECRET_KEY, DB_PATH, GOOGLE_CLIENT_ID,
|   |                              # GOOGLE_CLIENT_SECRET, APP_URL, SCOPES
|   |
|   |-- db.py                     # Module d'acces SQLite
|   |                              # init_db(), get_db()
|   |                              # CRUD: create_user, get_user, update_user
|   |                              # CRUD: create_gmail_account, get_gmail_accounts
|   |                              # CRUD: create_spreadsheet, get_spreadsheet
|   |                              # CRUD: create_scan_log, get_scan_logs
|   |                              # CRUD: create_processed_order, is_order_processed
|   |
|   |-- schema.sql                # Schema SQLite (CREATE TABLE)
|   |
|   |-- oauth_utils.py            # Gestion des tokens OAuth
|   |                              # get_credentials(gmail_account_id)
|   |                              # refresh_token_if_needed(gmail_account_id)
|   |                              # build_gmail_service(gmail_account_id)
|   |                              # build_sheets_service(user_id)
|   |
|   |-- sheets.py                 # Module Google Sheets
|   |                              # create_ticket_sheet(service)
|   |                              # create_vinted_sheet(service)
|   |                              # write_ticket_orders(service, sheet_id, orders)
|   |                              # write_vinted_sales(service, sheet_id, sales)
|   |                              # link_existing_sheet(url)
|   |
|   |-- scanner.py                # Script de scanning (cron)
|   |                              # scan_user(user_id)
|   |                              # scan_all_users()
|   |                              # main() avec argparse (--scan-all, --scan-user)
|   |
|   |-- parsers/
|   |   |-- __init__.py           # Expose les fonctions de parsing
|   |   |-- tickets.py            # Parsers Ticketmaster, Roland-Garros, SDF
|   |   |                          # parse_ticketmaster(subject, html)
|   |   |                          # parse_roland_garros(subject, html)
|   |   |                          # parse_stade_de_france(subject, html)
|   |   |                          # scan_tickets_gmail(service, processed_ids)
|   |   |
|   |   |-- vinted.py             # Parser Vinted (migre de IMAP vers Gmail API)
|   |   |                          # parse_vinted_email(html_content)
|   |   |                          # normalize_title(title)
|   |   |                          # find_matching_book(title, books, threshold)
|   |   |                          # scan_vinted_gmail(service, processed_ids)
|   |   |
|   |   |-- email_utils.py        # Utilitaires email partagees
|   |                              # extract_html_from_payload(payload)
|   |                              # get_email_headers(payload)
|   |
|   |-- templates/
|   |   |-- base.html             # Layout de base (head, nav, footer, scripts)
|   |   |-- index.html            # Landing page publique
|   |   |-- dashboard.html        # Espace client
|   |
|   |-- requirements.txt          # Dependances Python
|   |-- .env.example              # Variables d'environnement exemple
|
|-- frontend/
|   |-- css/
|   |   |-- style.css             # Styles globaux (variables, reset, layout)
|   |
|   |-- js/
|       |-- dashboard.js          # Logique client dashboard
|                                  # Appels API, mise a jour DOM
|
|-- static/                        # Fichiers statiques (images, favicon)
|
|-- deploy/
|   |-- gunicorn.conf.py          # Config Gunicorn
|   |-- nginx.conf                # Config Nginx
|   |-- billets-monitor.service   # Systemd unit file
|   |-- crontab.txt               # Cron job definition
|   |-- setup.sh                  # Script d'installation VPS
|
|-- tests/
|   |-- conftest.py               # Fixtures pytest (db en memoire, app test)
|   |-- fixtures/                 # Fichiers HTML de test (emails anonymises)
|   |   |-- ticketmaster_confirmation.html
|   |   |-- roland_garros_confirmation.html
|   |   |-- stade_de_france_confirmation.html
|   |   |-- vinted_transaction.html
|   |   |-- gmail_api_response.json
|   |
|   |-- test_parsers/
|   |   |-- test_tickets.py
|   |   |-- test_vinted.py
|   |   |-- test_email_utils.py
|   |
|   |-- test_db.py
|   |-- test_sheets.py
|   |-- test_integration/
|       |-- test_oauth.py
|       |-- test_scanner.py
|
|-- data/                          # Donnees runtime (cree automatiquement)
|   |-- monitor.db                # Base SQLite
|
|-- ARCHITECTURE.md               # Ce fichier
|-- .gitignore
|-- .env                          # Variables d'environnement (PAS commite)
```

---

## Configuration deploiement VPS

### Gunicorn (gunicorn.conf.py)

```python
# /opt/billets-monitor/deploy/gunicorn.conf.py

bind = "127.0.0.1:5000"
workers = 3
timeout = 120
accesslog = "/var/log/billets-monitor/access.log"
errorlog = "/var/log/billets-monitor/error.log"
loglevel = "info"
preload_app = True
```

### Nginx (nginx.conf)

```nginx
# /etc/nginx/sites-available/billets-monitor

server {
    listen 80;
    server_name monitor.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name monitor.example.com;

    ssl_certificate /etc/letsencrypt/live/monitor.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/monitor.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Headers de securite
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Fichiers statiques servis directement par Nginx
    location /static/ {
        alias /opt/billets-monitor/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /css/ {
        alias /opt/billets-monitor/frontend/css/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /js/ {
        alias /opt/billets-monitor/frontend/js/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Proxy vers Gunicorn
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

### Systemd (billets-monitor.service)

```ini
# /etc/systemd/system/billets-monitor.service

[Unit]
Description=Billets Monitor Flask Application
After=network.target

[Service]
User=billets
Group=billets
WorkingDirectory=/opt/billets-monitor/backend
ExecStart=/opt/billets-monitor/venv/bin/gunicorn --config /opt/billets-monitor/deploy/gunicorn.conf.py app:app
Restart=always
RestartSec=5
Environment="PATH=/opt/billets-monitor/venv/bin"
EnvironmentFile=/opt/billets-monitor/backend/.env

[Install]
WantedBy=multi-user.target
```

### Cron Job (crontab.txt)

```cron
# Scanner tous les utilisateurs toutes les 15 minutes
*/15 * * * * /opt/billets-monitor/venv/bin/python3 /opt/billets-monitor/backend/scanner.py --scan-all >> /var/log/billets-monitor/cron.log 2>&1

# Nettoyage des logs de scan > 90 jours (une fois par jour a 3h du matin)
0 3 * * * /opt/billets-monitor/venv/bin/python3 /opt/billets-monitor/backend/scanner.py --cleanup >> /var/log/billets-monitor/cron.log 2>&1
```

### Script d'installation VPS (setup.sh)

```bash
#!/bin/bash
# setup.sh — Installation sur VPS Hostinger (Ubuntu 22.04+)

set -e

APP_USER="billets"
APP_DIR="/opt/billets-monitor"
LOG_DIR="/var/log/billets-monitor"

echo "=== 1. Mise a jour systeme ==="
apt update && apt upgrade -y

echo "=== 2. Installation des dependances systeme ==="
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx sqlite3

echo "=== 3. Creation utilisateur systeme ==="
useradd -r -s /bin/false $APP_USER || true

echo "=== 4. Creation des repertoires ==="
mkdir -p $APP_DIR
mkdir -p $APP_DIR/data
mkdir -p $LOG_DIR

echo "=== 5. Copie des fichiers ==="
# (a executer apres rsync/git clone dans $APP_DIR)

echo "=== 6. Environnement virtuel Python ==="
python3 -m venv $APP_DIR/venv
$APP_DIR/venv/bin/pip install --upgrade pip
$APP_DIR/venv/bin/pip install -r $APP_DIR/backend/requirements.txt

echo "=== 7. Initialisation de la base de donnees ==="
$APP_DIR/venv/bin/python3 -c "
import sys; sys.path.insert(0, '$APP_DIR/backend')
from db import init_db; init_db()
"

echo "=== 8. Permissions ==="
chown -R $APP_USER:$APP_USER $APP_DIR
chown -R $APP_USER:$APP_USER $LOG_DIR
chmod 700 $APP_DIR/data
chmod 600 $APP_DIR/backend/.env

echo "=== 9. Configuration Nginx ==="
cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/billets-monitor
ln -sf /etc/nginx/sites-available/billets-monitor /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "=== 10. Configuration systemd ==="
cp $APP_DIR/deploy/billets-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable billets-monitor
systemctl start billets-monitor

echo "=== 11. Configuration cron ==="
crontab -u $APP_USER $APP_DIR/deploy/crontab.txt

echo "=== 12. SSL (Let's Encrypt) ==="
certbot --nginx -d monitor.example.com --non-interactive --agree-tos -m admin@example.com

echo "=== Installation terminee ==="
echo "Statut: $(systemctl status billets-monitor --no-pager)"
```

---

## Variables d'environnement (.env.example)

```bash
# Flask
SECRET_KEY=generer-une-cle-aleatoire-de-64-chars
FLASK_ENV=production

# Google OAuth
GOOGLE_CLIENT_ID=xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx

# Application
APP_URL=https://monitor.example.com
DB_PATH=../data/monitor.db
```

---

## Dependances Python (requirements.txt)

```
Flask==3.1.*
gunicorn==23.*
google-api-python-client==2.160.*
google-auth-oauthlib==1.2.*
google-auth-httplib2==0.2.*
gspread==6.*
beautifulsoup4==4.12.*
python-dotenv==1.0.*
```
