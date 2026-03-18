#!/bin/bash
# =============================================================================
# Billets Monitor MVP - Script de déploiement
# À exécuter sur le VPS Hostinger (Ubuntu 22.04+)
#
# Usage : sudo bash deploy.sh
# Le script est idempotent : peut être relancé sans danger.
# =============================================================================

set -e

# --- Configuration ---
DOMAIN="DOMAIN.com"
APP_DIR="/var/www/billets-monitor"
BACKEND_DIR="${APP_DIR}/backend"
DEPLOY_DIR="${APP_DIR}/deploy"
VENV_DIR="${APP_DIR}/venv"
LOG_DIR="/var/log/billets-monitor"
PID_DIR="/var/run/billets-monitor"
SERVICE_NAME="billets-monitor"
NGINX_CONF="${SERVICE_NAME}"

# --- Couleurs pour les messages ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[ATTENTION]${NC} $1"
}

error() {
    echo -e "${RED}[ERREUR]${NC} $1"
    exit 1
}

# --- Vérification des droits root ---
if [ "$EUID" -ne 0 ]; then
    error "Ce script doit être exécuté en tant que root (sudo bash deploy.sh)"
fi

echo ""
echo "=============================================="
echo "  Billets Monitor MVP - Déploiement"
echo "  Domaine : ${DOMAIN}"
echo "=============================================="
echo ""

# =============================================================================
# ÉTAPE 1 : Mise à jour du système
# =============================================================================
info "Étape 1/12 : Mise à jour des paquets système..."
apt-get update -qq
apt-get upgrade -y -qq
info "Système mis à jour."

# =============================================================================
# ÉTAPE 2 : Installation des dépendances système
# =============================================================================
info "Étape 2/12 : Installation des dépendances système..."
apt-get install -y -qq \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    nginx \
    certbot \
    python3-certbot-nginx \
    sqlite3 \
    curl \
    ufw

# Vérifier la version de Python
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
info "Python ${PYTHON_VERSION} installé."

# =============================================================================
# ÉTAPE 3 : Création de la structure de répertoires
# =============================================================================
info "Étape 3/12 : Création de la structure de répertoires..."

# Répertoire principal de l'application
mkdir -p "${APP_DIR}"
mkdir -p "${BACKEND_DIR}"
mkdir -p "${DEPLOY_DIR}"

# Répertoire de logs
mkdir -p "${LOG_DIR}"

# Répertoire PID
mkdir -p "${PID_DIR}"

info "Répertoires créés : ${APP_DIR}, ${LOG_DIR}, ${PID_DIR}"

# =============================================================================
# ÉTAPE 4 : Vérification des fichiers source
# =============================================================================
info "Étape 4/12 : Vérification des fichiers source..."

if [ ! -f "${BACKEND_DIR}/app.py" ]; then
    error "Les fichiers source ne sont pas présents dans ${BACKEND_DIR}.
Transférez d'abord les fichiers avec :
  rsync -avz --exclude='venv' --exclude='__pycache__' --exclude='.env' \\
    ./backend/ root@IP_VPS:${BACKEND_DIR}/
  rsync -avz ./deploy/ root@IP_VPS:${DEPLOY_DIR}/"
fi

if [ ! -f "${BACKEND_DIR}/requirements.txt" ]; then
    error "requirements.txt manquant dans ${BACKEND_DIR}"
fi

info "Fichiers source vérifiés."

# =============================================================================
# ÉTAPE 5 : Création de l'environnement virtuel Python
# =============================================================================
info "Étape 5/12 : Configuration de l'environnement virtuel Python..."

if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    info "Environnement virtuel créé."
else
    info "Environnement virtuel déjà existant, mise à jour..."
fi

# Installer / mettre à jour les dépendances
"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${BACKEND_DIR}/requirements.txt" -q

info "Dépendances Python installées."

# =============================================================================
# ÉTAPE 6 : Initialisation de la base de données SQLite
# =============================================================================
info "Étape 6/12 : Initialisation de la base de données..."

if [ ! -f "${BACKEND_DIR}/billets_monitor.db" ]; then
    cd "${BACKEND_DIR}"
    "${VENV_DIR}/bin/python" -c "import database; database.init_db()"
    info "Base de données SQLite initialisée."
else
    info "Base de données déjà existante, aucune action."
fi

# =============================================================================
# ÉTAPE 7 : Vérification du fichier .env
# =============================================================================
info "Étape 7/12 : Vérification du fichier .env..."

if [ ! -f "${BACKEND_DIR}/.env" ]; then
    warn "Le fichier .env n'existe pas dans ${BACKEND_DIR}."
    warn "Créez-le avec vos variables d'environnement :"
    echo ""
    echo "  cat > ${BACKEND_DIR}/.env << 'ENVEOF'"
    echo "  SECRET_KEY=votre_cle_secrete_generee"
    echo "  GOOGLE_CLIENT_ID=votre_client_id"
    echo "  GOOGLE_CLIENT_SECRET=votre_client_secret"
    echo "  APP_URL=https://${DOMAIN}"
    echo "  FLASK_ENV=production"
    echo "  ENVEOF"
    echo ""
    warn "Le déploiement continue mais l'application ne démarrera pas sans .env."
else
    info "Fichier .env trouvé."
fi

# =============================================================================
# ÉTAPE 8 : Configuration Nginx
# =============================================================================
info "Étape 8/12 : Configuration de Nginx..."

# Copier la configuration Nginx
cp "${DEPLOY_DIR}/nginx.conf" "/etc/nginx/sites-available/${NGINX_CONF}"

# Remplacer le placeholder DOMAIN.com par le vrai domaine
sed -i "s/DOMAIN\.com/${DOMAIN}/g" "/etc/nginx/sites-available/${NGINX_CONF}"

# Créer le lien symbolique (supprimer l'ancien si existant)
if [ -L "/etc/nginx/sites-enabled/${NGINX_CONF}" ]; then
    rm "/etc/nginx/sites-enabled/${NGINX_CONF}"
fi
ln -s "/etc/nginx/sites-available/${NGINX_CONF}" "/etc/nginx/sites-enabled/${NGINX_CONF}"

# Supprimer le site par défaut s'il existe
if [ -L "/etc/nginx/sites-enabled/default" ]; then
    rm "/etc/nginx/sites-enabled/default"
    info "Site Nginx par défaut désactivé."
fi

# Créer le répertoire pour Certbot
mkdir -p /var/www/certbot

# Tester la configuration Nginx (ignorer l'erreur SSL si les certs n'existent pas encore)
# On utilise d'abord une config HTTP-only temporaire
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    warn "Certificats SSL pas encore générés. Configuration HTTP temporaire..."
    # Créer une config temporaire HTTP-only pour le démarrage initial
    cat > "/etc/nginx/sites-available/${NGINX_CONF}" << TMPNGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /static/ {
        alias ${BACKEND_DIR}/static/;
        expires 30d;
        access_log off;
    }
}
TMPNGINX
    info "Configuration HTTP temporaire créée (SSL sera ajouté avec certbot)."
fi

# Tester et recharger Nginx
nginx -t || error "La configuration Nginx est invalide."
systemctl enable nginx
systemctl reload nginx

info "Nginx configuré et rechargé."

# =============================================================================
# ÉTAPE 9 : Configuration du service Systemd
# =============================================================================
info "Étape 9/12 : Configuration du service Systemd..."

# Copier le fichier de service
cp "${DEPLOY_DIR}/billets-monitor.service" "/etc/systemd/system/${SERVICE_NAME}.service"

# Recharger systemd
systemctl daemon-reload

# Activer le service au démarrage
systemctl enable "${SERVICE_NAME}"

# Démarrer ou redémarrer le service
if [ -f "${BACKEND_DIR}/.env" ]; then
    systemctl restart "${SERVICE_NAME}" || warn "Le service n'a pas pu démarrer. Vérifiez le .env et les logs."
    info "Service ${SERVICE_NAME} démarré."
else
    warn "Service configuré mais non démarré (fichier .env manquant)."
fi

info "Service Systemd configuré."

# =============================================================================
# ÉTAPE 10 : Configuration du Crontab
# =============================================================================
info "Étape 10/12 : Configuration du crontab..."

# Vérifier si les entrées cron existent déjà
CRON_MARKER="billets-monitor"
EXISTING_CRON=$(crontab -u www-data -l 2>/dev/null || true)

if echo "${EXISTING_CRON}" | grep -q "${CRON_MARKER}"; then
    info "Entrées crontab déjà présentes, mise à jour..."
    # Supprimer les anciennes entrées et ajouter les nouvelles
    (echo "${EXISTING_CRON}" | grep -v "billets-monitor"; cat "${DEPLOY_DIR}/crontab.txt") | crontab -u www-data -
else
    # Ajouter les nouvelles entrées
    (echo "${EXISTING_CRON}"; echo ""; cat "${DEPLOY_DIR}/crontab.txt") | crontab -u www-data -
fi

info "Crontab configuré (scan toutes les 10 minutes)."

# =============================================================================
# ÉTAPE 11 : Permissions des fichiers
# =============================================================================
info "Étape 11/12 : Configuration des permissions..."

# Propriétaire www-data pour tout le projet
chown -R www-data:www-data "${APP_DIR}"
chown -R www-data:www-data "${LOG_DIR}"
chown -R www-data:www-data "${PID_DIR}"

# Permissions répertoires
find "${APP_DIR}" -type d -exec chmod 755 {} \;

# Permissions fichiers
find "${APP_DIR}" -type f -exec chmod 644 {} \;

# Rendre les scripts exécutables
chmod 755 "${DEPLOY_DIR}/deploy.sh"
chmod 755 "${VENV_DIR}/bin/"*

# Protéger le fichier .env
if [ -f "${BACKEND_DIR}/.env" ]; then
    chmod 600 "${BACKEND_DIR}/.env"
fi

# Protéger la base de données
if [ -f "${BACKEND_DIR}/billets_monitor.db" ]; then
    chmod 660 "${BACKEND_DIR}/billets_monitor.db"
fi

info "Permissions configurées."

# =============================================================================
# ÉTAPE 12 : Firewall UFW
# =============================================================================
info "Étape 12/12 : Configuration du firewall..."

# Autoriser SSH, HTTP et HTTPS
ufw allow OpenSSH > /dev/null 2>&1
ufw allow 'Nginx Full' > /dev/null 2>&1

# Activer le firewall si pas déjà actif
if ! ufw status | grep -q "Status: active"; then
    echo "y" | ufw enable > /dev/null 2>&1
fi

info "Firewall configuré (SSH, HTTP, HTTPS autorisés)."

# =============================================================================
# SSL avec Let's Encrypt (interactif)
# =============================================================================
echo ""
echo "=============================================="
echo "  Configuration SSL avec Let's Encrypt"
echo "=============================================="
echo ""

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    info "Certificats SSL déjà présents pour ${DOMAIN}."
    # Restaurer la configuration Nginx complète avec SSL
    cp "${DEPLOY_DIR}/nginx.conf" "/etc/nginx/sites-available/${NGINX_CONF}"
    sed -i "s/DOMAIN\.com/${DOMAIN}/g" "/etc/nginx/sites-available/${NGINX_CONF}"
    nginx -t && systemctl reload nginx
    info "Configuration Nginx SSL restaurée."
else
    echo "Voulez-vous configurer SSL maintenant ? (o/n)"
    echo "(Le domaine ${DOMAIN} doit pointer vers ce serveur)"
    read -r SETUP_SSL

    if [ "${SETUP_SSL}" = "o" ] || [ "${SETUP_SSL}" = "O" ] || [ "${SETUP_SSL}" = "oui" ]; then
        info "Lancement de Certbot..."
        certbot --nginx -d "${DOMAIN}" -d "www.${DOMAIN}" --non-interactive --agree-tos --redirect --email "admin@${DOMAIN}" || {
            warn "Certbot a échoué. Vous pouvez le relancer manuellement :"
            echo "  sudo certbot --nginx -d ${DOMAIN} -d www.${DOMAIN}"
        }

        # Vérifier le renouvellement automatique
        systemctl enable certbot.timer 2>/dev/null || true
        info "SSL configuré. Renouvellement automatique activé."
    else
        warn "SSL non configuré. Lancez manuellement plus tard :"
        echo "  sudo certbot --nginx -d ${DOMAIN} -d www.${DOMAIN}"
    fi
fi

# =============================================================================
# Résumé et prochaines étapes
# =============================================================================
echo ""
echo "=============================================="
echo -e "  ${GREEN}Déploiement terminé !${NC}"
echo "=============================================="
echo ""
echo "Checklist post-déploiement :"
echo "----------------------------"
echo ""

if [ -f "${BACKEND_DIR}/.env" ]; then
    echo -e "  [OK] Fichier .env configuré"
else
    echo -e "  ${RED}[TODO]${NC} Créer le fichier .env : nano ${BACKEND_DIR}/.env"
fi

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    echo -e "  [OK] SSL configuré pour ${DOMAIN}"
else
    echo -e "  ${YELLOW}[TODO]${NC} Configurer SSL : sudo certbot --nginx -d ${DOMAIN} -d www.${DOMAIN}"
fi

echo ""
echo "Commandes utiles :"
echo "----------------------------"
echo "  Statut du service    : sudo systemctl status ${SERVICE_NAME}"
echo "  Logs application     : sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Logs Gunicorn        : sudo tail -f ${LOG_DIR}/gunicorn-error.log"
echo "  Logs Cron            : sudo tail -f ${LOG_DIR}/cron.log"
echo "  Logs Nginx           : sudo tail -f /var/log/nginx/billets-monitor.error.log"
echo "  Redémarrer le service: sudo systemctl restart ${SERVICE_NAME}"
echo "  Recharger Nginx      : sudo systemctl reload nginx"
echo ""
echo "URLs :"
echo "----------------------------"
echo "  HTTP  : http://${DOMAIN}"
echo "  HTTPS : https://${DOMAIN}"
echo ""
echo "Vérification rapide :"
echo "  curl -I http://${DOMAIN}"
echo "  curl -I https://${DOMAIN}"
echo ""
