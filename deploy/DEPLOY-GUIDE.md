# Guide de Deploiement - Billets Monitor MVP

Guide complet pour deployer l'application Billets Monitor sur un VPS Hostinger (Ubuntu 22.04+).

---

## 1. Prerequis

### Cote Hostinger
- **VPS Hostinger** avec Ubuntu 22.04 ou superieur
- **Minimum** : 1 vCPU, 1 Go RAM, 20 Go SSD
- **Acces SSH** root configure (cle SSH recommandee)
- **Adresse IP** du VPS (visible dans le panel Hostinger)

### Cote domaine
- **Nom de domaine** achete (chez Hostinger ou ailleurs)
- **Acces DNS** pour configurer les enregistrements A

### Sur votre machine locale
- Les fichiers du projet Billets Monitor MVP
- Un terminal avec SSH et rsync installes

---

## 2. Configurer le DNS

Avant le deploiement, pointez votre domaine vers le VPS.

### Dans le panel DNS de votre registrar

| Type | Nom | Valeur | TTL |
|------|-----|--------|-----|
| A | @ | `IP_DE_VOTRE_VPS` | 3600 |
| A | www | `IP_DE_VOTRE_VPS` | 3600 |

> **Note** : La propagation DNS peut prendre de 5 minutes a 48 heures. Verifiez avec :
> ```bash
> dig +short DOMAIN.com
> ```

---

## 3. Transferer les fichiers vers le VPS

### Option A : rsync (recommande)

```bash
# Depuis la racine du projet billets-monitor-mvp/

# 1. Transferer le backend
rsync -avz --exclude='venv' \
           --exclude='__pycache__' \
           --exclude='.env' \
           --exclude='*.db' \
           --exclude='*.sqlite3' \
           ./backend/ root@IP_VPS:/var/www/billets-monitor/backend/

# 2. Transferer les fichiers de deploiement
rsync -avz ./deploy/ root@IP_VPS:/var/www/billets-monitor/deploy/
```

### Option B : scp

```bash
# Creer le repertoire sur le VPS
ssh root@IP_VPS "mkdir -p /var/www/billets-monitor/{backend,deploy}"

# Transferer les fichiers
scp -r ./backend/* root@IP_VPS:/var/www/billets-monitor/backend/
scp -r ./deploy/* root@IP_VPS:/var/www/billets-monitor/deploy/
```

### Option C : Git (si le projet est sur GitHub)

```bash
ssh root@IP_VPS
cd /var/www
git clone https://github.com/VOTRE_REPO/billets-monitor-mvp.git billets-monitor
```

---

## 4. Se connecter au VPS et deployer

### 4.1 Connexion SSH

```bash
ssh root@IP_VPS
```

### 4.2 Configurer le domaine dans le script

```bash
nano /var/www/billets-monitor/deploy/deploy.sh
```

Modifiez la variable `DOMAIN` en haut du fichier :
```bash
DOMAIN="votre-domaine.com"
```

### 4.3 Lancer le deploiement

```bash
cd /var/www/billets-monitor/deploy
chmod +x deploy.sh
sudo bash deploy.sh
```

Le script va :
1. Mettre a jour le systeme
2. Installer Python, Nginx, Certbot
3. Creer l'environnement virtuel
4. Installer les dependances
5. Initialiser la base de donnees
6. Configurer Nginx
7. Configurer le service Systemd
8. Configurer le crontab
9. Configurer les permissions
10. Configurer le firewall
11. Proposer l'installation SSL

---

## 5. Configurer le fichier .env

Le fichier `.env` contient les secrets de l'application. Ne le transférez **jamais** via Git.

```bash
nano /var/www/billets-monitor/backend/.env
```

Contenu :
```env
# Cle secrete Flask (generez-en une unique)
SECRET_KEY=VOTRE_CLE_SECRETE_ICI

# Google OAuth 2.0
GOOGLE_CLIENT_ID=votre_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=votre_client_secret

# URL de l'application (avec https apres certbot)
APP_URL=https://votre-domaine.com

# Environnement
FLASK_ENV=production
```

### Generer une cle secrete

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Configurer Google OAuth

1. Allez sur [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Dans votre projet OAuth, ajoutez les URIs de redirection autorises :
   - `https://votre-domaine.com/oauth/callback`
3. Copiez le Client ID et Client Secret dans le `.env`

### Appliquer la configuration

```bash
# Proteger le fichier
chmod 600 /var/www/billets-monitor/backend/.env
chown www-data:www-data /var/www/billets-monitor/backend/.env

# Redemarrer le service
sudo systemctl restart billets-monitor
```

---

## 6. Configurer SSL avec Let's Encrypt

### Si vous avez refuse le SSL pendant le deploiement

```bash
sudo certbot --nginx -d votre-domaine.com -d www.votre-domaine.com
```

### Puis restaurer la configuration Nginx complete

```bash
# Copier la config SSL complete
sudo cp /var/www/billets-monitor/deploy/nginx.conf /etc/nginx/sites-available/billets-monitor

# Remplacer le placeholder par votre domaine
sudo sed -i 's/DOMAIN\.com/votre-domaine.com/g' /etc/nginx/sites-available/billets-monitor

# Tester et recharger
sudo nginx -t
sudo systemctl reload nginx
```

### Verifier le renouvellement automatique

```bash
# Tester le renouvellement (dry run)
sudo certbot renew --dry-run

# Verifier le timer
sudo systemctl status certbot.timer
```

---

## 7. Verification

### 7.1 Verifier le service

```bash
# Statut du service
sudo systemctl status billets-monitor

# Verifier que Gunicorn ecoute
ss -tlnp | grep 8000
```

### 7.2 Tester l'application

```bash
# Test HTTP local
curl -I http://127.0.0.1:8000

# Test via le domaine (HTTP)
curl -I http://votre-domaine.com

# Test via le domaine (HTTPS)
curl -I https://votre-domaine.com

# Verifier la redirection HTTP -> HTTPS
curl -I http://votre-domaine.com 2>&1 | grep Location
```

### 7.3 Verifier les en-tetes de securite

```bash
curl -sI https://votre-domaine.com | grep -E "(X-Frame|X-Content|X-XSS|Strict-Transport)"
```

Sortie attendue :
```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
```

### 7.4 Verifier le cron

```bash
# Lister les taches cron
sudo crontab -u www-data -l

# Executer un scan manuellement
cd /var/www/billets-monitor/backend
sudo -u www-data /var/www/billets-monitor/venv/bin/python cron.py

# Verifier les logs
sudo tail -f /var/log/billets-monitor/cron.log
```

---

## 8. Monitoring et Maintenance

### 8.1 Consulter les logs

```bash
# Logs de l'application (systemd/journal)
sudo journalctl -u billets-monitor -f

# Logs Gunicorn
sudo tail -f /var/log/billets-monitor/gunicorn-error.log
sudo tail -f /var/log/billets-monitor/gunicorn-access.log

# Logs du cron
sudo tail -f /var/log/billets-monitor/cron.log

# Logs Nginx
sudo tail -f /var/log/nginx/billets-monitor.access.log
sudo tail -f /var/log/nginx/billets-monitor.error.log
```

### 8.2 Gestion du service

```bash
# Demarrer
sudo systemctl start billets-monitor

# Arreter
sudo systemctl stop billets-monitor

# Redemarrer
sudo systemctl restart billets-monitor

# Recharger (sans downtime)
sudo systemctl reload billets-monitor

# Desactiver le demarrage automatique
sudo systemctl disable billets-monitor
```

### 8.3 Mettre a jour l'application

```bash
# 1. Transferer les nouveaux fichiers
rsync -avz --exclude='venv' \
           --exclude='__pycache__' \
           --exclude='.env' \
           --exclude='*.db' \
           ./backend/ root@IP_VPS:/var/www/billets-monitor/backend/

# 2. Sur le VPS : mettre a jour les dependances si necessaire
ssh root@IP_VPS
cd /var/www/billets-monitor
source venv/bin/activate
pip install -r backend/requirements.txt

# 3. Redemarrer le service
sudo systemctl restart billets-monitor

# 4. Verifier
sudo systemctl status billets-monitor
```

### 8.4 Sauvegarder la base de donnees

```bash
# Sauvegarde manuelle
sudo cp /var/www/billets-monitor/backend/billets_monitor.db \
        /var/www/billets-monitor/backend/billets_monitor.db.backup.$(date +%Y%m%d)

# Sauvegarde automatique (ajouter au crontab)
# 0 2 * * * cp /var/www/billets-monitor/backend/billets_monitor.db /var/www/billets-monitor/backend/backups/billets_monitor.db.$(date +\%Y\%m\%d)
```

### 8.5 Surveiller les ressources

```bash
# Utilisation memoire
free -h

# Utilisation disque
df -h

# Processus Gunicorn
ps aux | grep gunicorn

# Connexions actives
ss -tlnp
```

---

## 9. Depannage

### L'application ne demarre pas

```bash
# Verifier les logs d'erreur
sudo journalctl -u billets-monitor --no-pager -n 50

# Verifier que le .env existe et est lisible
sudo -u www-data cat /var/www/billets-monitor/backend/.env

# Tester l'application manuellement
cd /var/www/billets-monitor/backend
sudo -u www-data /var/www/billets-monitor/venv/bin/python -c "from app import app; print('OK')"
```

### Erreur 502 Bad Gateway

```bash
# Gunicorn ne tourne pas
sudo systemctl status billets-monitor
sudo systemctl restart billets-monitor

# Verifier que le port 8000 est ouvert
ss -tlnp | grep 8000
```

### Erreur de permission

```bash
# Re-appliquer les permissions
sudo chown -R www-data:www-data /var/www/billets-monitor
sudo chown -R www-data:www-data /var/log/billets-monitor
sudo chmod 600 /var/www/billets-monitor/backend/.env
```

### Certbot echoue

```bash
# Verifier que le DNS pointe bien vers le VPS
dig +short votre-domaine.com

# Verifier que le port 80 est ouvert
sudo ufw status
curl -I http://votre-domaine.com

# Relancer certbot en mode verbose
sudo certbot --nginx -d votre-domaine.com -v
```

### Le cron ne fonctionne pas

```bash
# Verifier que le crontab est installe
sudo crontab -u www-data -l

# Tester le script manuellement
cd /var/www/billets-monitor/backend
sudo -u www-data /var/www/billets-monitor/venv/bin/python cron.py

# Verifier les permissions
ls -la /var/www/billets-monitor/backend/cron.py
```

---

## 10. Architecture du deploiement

```
Internet
    |
    v
[Nginx :80/:443]  --- SSL (Let's Encrypt)
    |                  Fichiers statiques
    |                  En-tetes securite
    v
[Gunicorn :8000]  --- 2 workers sync
    |                  Timeout 120s
    v
[Flask App]       --- app.py
    |                  SQLite DB
    v
[Cron Job]        --- toutes les 10 min
                      cron.py -> scan
```

---

## Commandes rapides

```bash
# Tout redemarrer
sudo systemctl restart billets-monitor && sudo systemctl reload nginx

# Voir les logs en temps reel
sudo journalctl -u billets-monitor -f

# Verifier que tout tourne
sudo systemctl status billets-monitor nginx certbot.timer

# Backup rapide
sudo cp /var/www/billets-monitor/backend/billets_monitor.db ~/backup-$(date +%Y%m%d).db
```
