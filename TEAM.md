# Monitorz — Agent Team Playbook

Team : `monitorz-overhaul`
Lead : **claude-opus-4-6** (coordination, architecture, décisions)
Workers : **claude-sonnet-4-6** (implémentation parallèle)
Projet : `/Users/cesarrevert/Desktop/billets-monitor-mvp/backend`

---

## Composition de la team

| Agent | Modèle | Domaine | Fichiers |
|-------|--------|---------|----------|
| **team-lead** | Opus 4.6 | Coordination, architecture, review final | Tout |
| **python-pro** | Sonnet 4.6 | Flask routes, helpers, logic métier | `app.py`, `routes/`, `helpers.py`, `scanner.py` |
| **database-optimizer** | Sonnet 4.6 | SQLite schema, queries, migrations | `database.py`, `database_sqlite.py`, `schema.sql`, `monitor.db` |
| **test-automator** | Sonnet 4.6 | Tests unitaires parsers + routes | `tests/`, `conftest.py` |
| **security-auditor** | Sonnet 4.6 | OAuth Google, auth routes, tokens, secrets | `routes/auth.py`, `config.py`, `.env` |
| **frontend-developer** | Sonnet 4.6 | Jinja templates, dashboard HTML/CSS/JS | `templates/`, `static/` |

---

## Comment spawner la team

### Option 1 — Team complète (gros chantier)

```
Lance la team monitorz-overhaul avec ces agents en parallèle :
- python-pro (Sonnet) : routes/ et scanner.py
- database-optimizer (Sonnet) : database.py et schema
- test-automator (Sonnet) : tests/
- security-auditor (Sonnet) : routes/auth.py
- frontend-developer (Sonnet) : templates/
Le team-lead (Opus) coordonne et valide les PRs entre agents.
```

### Option 2 — Team ciblée par domaine

**Parsers uniquement :**
```
Spawne python-pro + test-automator sur parsers/
python-pro refactorise, test-automator écrit les tests simultanément.
```

**Feature complète (ex: dark mode) :**
```
Spawne frontend-developer (templates/) + python-pro (routes/api.py) en parallèle.
team-lead coordonne et merge.
```

**Debug urgence :**
```
Spawne debugger + error-detective + python-pro.
debugger identifie, python-pro corrige, test-automator valide.
```

---

## Règles de la team

- **team-lead** décide de l'architecture, les workers implémentent
- Chaque worker travaille sur son domaine uniquement — pas de cross-domain sans accord lead
- Workers utilisent `SendMessage` au lead pour signaler blocages
- Lead fait le merge final et valide la cohérence
- Modèle lead : **Opus 4.6** — ne jamais downgrader en Sonnet pour le lead
- Workers : **Sonnet 4.6** — suffisant pour l'implémentation

---

## Stack Monitorz (référence rapide)

```
Flask + Python 3.14
SQLite (monitor.db) + schema.sql
Google APIs : Gmail, Sheets, OAuth
Jinja2 templates (dashboard.html monolithique)
venv : venv/bin/python3
Port : 5050
Auth DB keys : oauth_token, oauth_refresh_token
Scanner : USER_ENTERED (pas RAW) pour Sheets
WTS Template B : artiste UPPERCASE, *Venue Date:* bold
```
