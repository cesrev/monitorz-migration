# =============================================================================
# Billets Monitor MVP - Configuration Gunicorn
# Serveur WSGI pour l'application Flask
# =============================================================================

# Adresse de binding (uniquement localhost, Nginx gère le trafic externe)
bind = "127.0.0.1:8000"

# Nombre de workers (2 pour un VPS avec ressources limitées)
# Règle générale : (2 x CPU cores) + 1, mais on reste à 2 pour un petit VPS
workers = 2

# Type de worker (sync convient pour Flask avec peu de trafic)
worker_class = "sync"

# Timeout de 120 secondes (les scans peuvent être longs)
timeout = 120

# Timeout gracieux pour le redémarrage des workers
graceful_timeout = 30

# Nombre max de requêtes avant restart du worker (évite les fuites mémoire)
max_requests = 1000
max_requests_jitter = 50

# Précharger l'application (réduit la mémoire avec fork)
preload_app = True

# --- Logging ---
accesslog = "/var/log/billets-monitor/gunicorn-access.log"
errorlog = "/var/log/billets-monitor/gunicorn-error.log"
loglevel = "info"

# Format des logs d'accès
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# --- Sécurité ---
# Limiter la taille des en-têtes HTTP
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Fichier PID
pidfile = "/var/run/billets-monitor/gunicorn.pid"

# Nom du processus (visible dans ps/htop)
proc_name = "billets-monitor"
