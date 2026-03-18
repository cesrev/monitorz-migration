-- ============================================================
-- Billets & Vinted Monitor — Self-Service MVP
-- Schema SQLite
-- ============================================================
-- Execution : sqlite3 data/monitor.db < backend/schema.sql
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- TABLE : users
-- Utilisateurs inscrits via Google OAuth
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL DEFAULT '',
    picture         TEXT    NOT NULL DEFAULT '',
    monitoring_type TEXT    NOT NULL CHECK (monitoring_type IN ('tickets', 'vinted')),
    plan            TEXT    NOT NULL DEFAULT 'starter' CHECK (plan IN ('starter', 'pro')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- ============================================================
-- TABLE : gmail_accounts
-- Comptes Gmail connectes via OAuth (1 user peut en avoir N)
-- Le compte de connexion initial a is_primary = 1
-- Les comptes ajoutes ensuite ont is_primary = 0
-- ============================================================
CREATE TABLE IF NOT EXISTS gmail_accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    email               TEXT    NOT NULL,
    oauth_token         TEXT    NOT NULL,
    oauth_refresh_token TEXT    NOT NULL,
    token_expiry        TEXT,
    is_primary          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, email)
);

CREATE INDEX IF NOT EXISTS idx_gmail_accounts_user_id ON gmail_accounts(user_id);

-- ============================================================
-- TABLE : spreadsheets
-- Google Sheets lies a un utilisateur
-- is_auto_created = 1 si cree automatiquement a l'inscription
-- is_auto_created = 0 si lie manuellement ("Lier un Sheet existant")
-- ============================================================
CREATE TABLE IF NOT EXISTS spreadsheets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    spreadsheet_id  TEXT    NOT NULL,
    spreadsheet_url TEXT    NOT NULL,
    is_auto_created INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_spreadsheets_user_id ON spreadsheets(user_id);

-- ============================================================
-- TABLE : scan_logs
-- Historique de chaque execution de scan
-- scan_type : 'ticketmaster', 'roland-garros', 'stade-de-france', 'vinted'
-- status : 'success', 'error', 'no_new_orders'
-- ============================================================
CREATE TABLE IF NOT EXISTS scan_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    gmail_account_id  INTEGER,
    scan_type         TEXT    NOT NULL,
    orders_found      INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL CHECK (status IN ('pending', 'running', 'success', 'error', 'no_new_orders')),
    error_message     TEXT,
    scanned_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (gmail_account_id) REFERENCES gmail_accounts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_scan_logs_user_id ON scan_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_scan_logs_scanned_at ON scan_logs(scanned_at);

-- ============================================================
-- TABLE : processed_orders
-- Commandes deja traitees (pour deduplication)
-- source : 'ticketmaster', 'roland-garros', 'stade-de-france', 'vinted'
-- email_id : ID du message Gmail (msg_id) pour reference
-- ============================================================
CREATE TABLE IF NOT EXISTS processed_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    order_number    TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    email_id        TEXT,
    processed_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, order_number, source)
);

CREATE INDEX IF NOT EXISTS idx_processed_orders_user_id ON processed_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_processed_orders_lookup ON processed_orders(user_id, order_number, source);

-- ============================================================
-- TRIGGER : updated_at sur users
-- Met a jour automatiquement updated_at a chaque UPDATE
-- ============================================================
CREATE TRIGGER IF NOT EXISTS trg_users_updated_at
    AFTER UPDATE ON users
    FOR EACH ROW
    WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE users SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = NEW.id;
END;
