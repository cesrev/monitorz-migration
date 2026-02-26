# Monitorz

**Vends plus, gere moins.**

SaaS de monitoring automatique des emails de confirmation (billets, Vinted) avec export vers Google Sheets.

## Stack

- **Backend** : Python Flask
- **Database** : SQLite
- **Auth** : Google OAuth 2.0
- **APIs** : Gmail API, Google Sheets API
- **Templates** : Jinja2 + CSS custom (dark theme)

## Fonctionnalites

- Connexion Google OAuth (Gmail + Sheets + Drive)
- Scan automatique des emails de confirmation (toutes les heures)
- Scan manuel depuis le dashboard
- Extraction des donnees : evenement, prix, date, lieu, numero de commande
- Export automatique vers Google Sheets (headers formattees, bold + fond grise)
- Detection des doublons (deduplication par email_id)
- Dashboard client avec stats, historique des scans, parametres
- Panel admin pour voir tous les clients connectes
- Plans Starter (20€) et Pro (30€) avec features differenciees

## Structure

```
billets-monitor-mvp/
├── backend/
│   ├── app.py              # Flask app, routes, OAuth, scheduler
│   ├── database.py         # SQLite CRUD
│   ├── scanner.py          # Gmail scanner + Sheets writer
│   ├── config.py           # Configuration (env vars)
│   ├── parsers/            # Email parsers (Ticketmaster, Fnac, etc.)
│   ├── templates/          # Jinja2 templates (landing, login, dashboard, admin)
│   ├── static/             # CSS, JS, images
│   ├── .env.example        # Variables d'environnement requises
│   └── requirements.txt    # Dependencies Python
├── deploy/                 # Scripts de deploiement (Hostinger VPS)
├── frontend/               # Assets frontend supplementaires
├── ARCHITECTURE.md         # Documentation architecture
└── SETUP-GOOGLE-CLOUD.md   # Guide config Google Cloud Console
```

## Installation

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Remplir .env avec les credentials Google OAuth
python app.py
```

Le serveur demarre sur `http://localhost:5050`.

## Variables d'environnement

```
SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
APP_URL=http://localhost:5050
ADMIN_EMAILS=ton@email.com
```
