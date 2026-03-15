-- ============================================
-- Monitorz — Supabase PostgreSQL Schema
-- Run this in the Supabase SQL Editor
-- ============================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- USERS
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    picture TEXT DEFAULT '',
    monitoring_type TEXT NOT NULL CHECK (monitoring_type IN ('tickets', 'vinted')),
    plan TEXT NOT NULL DEFAULT 'starter' CHECK (plan IN ('starter', 'pro')),
    billing_period TEXT NOT NULL DEFAULT 'monthly' CHECK (billing_period IN ('monthly', 'yearly')),
    scan_frequency INTEGER NOT NULL DEFAULT 10,
    alert_days_before INTEGER NOT NULL DEFAULT 7,
    dormant_days_threshold INTEGER NOT NULL DEFAULT 30,
    monitoring_paused INTEGER NOT NULL DEFAULT 0,
    onboarding_complete INTEGER NOT NULL DEFAULT 0,
    monthly_costs REAL NOT NULL DEFAULT 0,

    -- Trial & Referral
    trial_started_at TIMESTAMPTZ DEFAULT NULL,
    trial_ends_at TIMESTAMPTZ DEFAULT NULL,
    is_trial_active INTEGER NOT NULL DEFAULT 0,
    referral_code TEXT DEFAULT '',
    referred_by TEXT DEFAULT '',
    referral_count INTEGER DEFAULT 0,

    -- Company & Invoice
    company_name TEXT DEFAULT '',
    company_address TEXT DEFAULT '',
    company_phone TEXT DEFAULT '',
    company_email TEXT DEFAULT '',
    company_siret TEXT DEFAULT '',
    company_tva_number TEXT DEFAULT '',
    company_iban TEXT DEFAULT '',
    company_bic TEXT DEFAULT '',
    company_tva_rate REAL DEFAULT 20.0,
    invoice_prefix TEXT DEFAULT 'INV',
    invoice_counter INTEGER DEFAULT 0,
    invoice_footer TEXT DEFAULT '',

    -- Extension config
    ext_secret TEXT DEFAULT '',
    ext_secret_hash TEXT DEFAULT '',
    ext_msg_enabled INTEGER DEFAULT 1,
    ext_msg_template TEXT DEFAULT '',
    ext_msg_quota_daily INTEGER DEFAULT 50,
    ext_poll_interval_min INTEGER DEFAULT 5,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code);
CREATE INDEX IF NOT EXISTS idx_users_is_trial_active ON users(is_trial_active);

-- ============================================
-- GMAIL ACCOUNTS
-- ============================================
CREATE TABLE IF NOT EXISTS gmail_accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    oauth_token TEXT,
    oauth_refresh_token TEXT,
    token_expiry TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gmail_accounts_user ON gmail_accounts(user_id);

-- ============================================
-- SPREADSHEETS
-- ============================================
CREATE TABLE IF NOT EXISTS spreadsheets (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    spreadsheet_id TEXT NOT NULL,
    spreadsheet_url TEXT NOT NULL,
    is_auto_created INTEGER NOT NULL DEFAULT 1,
    monitoring_type TEXT NOT NULL DEFAULT 'tickets',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spreadsheets_user ON spreadsheets(user_id);

-- ============================================
-- SCAN LOGS
-- ============================================
CREATE TABLE IF NOT EXISTS scan_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gmail_account_id BIGINT REFERENCES gmail_accounts(id) ON DELETE SET NULL,
    scan_type TEXT NOT NULL,
    orders_found INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    monitoring_type TEXT NOT NULL DEFAULT 'tickets',
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_logs_user ON scan_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_scan_logs_user_type ON scan_logs(user_id, monitoring_type, scanned_at);

-- ============================================
-- PROCESSED ORDERS
-- ============================================
CREATE TABLE IF NOT EXISTS processed_orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_number TEXT NOT NULL,
    source TEXT NOT NULL,
    email_id TEXT NOT NULL,
    monitoring_type TEXT NOT NULL DEFAULT 'tickets',
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_orders_unique
    ON processed_orders(user_id, email_id, monitoring_type);
CREATE INDEX IF NOT EXISTS idx_processed_orders_user_type
    ON processed_orders(user_id, monitoring_type);

-- ============================================
-- NOTIFICATIONS
-- ============================================
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('event_soon', 'dormant_stock', 'scan_result', 'info')),
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    read INTEGER NOT NULL DEFAULT 0,
    reference_key TEXT DEFAULT '',
    monitoring_type TEXT NOT NULL DEFAULT 'tickets',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read);
CREATE INDEX IF NOT EXISTS idx_notifications_user_type_read ON notifications(user_id, monitoring_type, read);

-- ============================================
-- SERVICES
-- ============================================
CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_email TEXT NOT NULL,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    unit_price_ht REAL DEFAULT 0.0,
    tva_rate REAL DEFAULT 20.0,
    description TEXT DEFAULT '',
    position INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_email);
CREATE INDEX IF NOT EXISTS idx_services_user_id ON services(user_id);

-- ============================================
-- VINTED SESSIONS
-- ============================================
CREATE TABLE IF NOT EXISTS vinted_sessions (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'fr',
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- EXTENSION LOGS
-- ============================================
CREATE TABLE IF NOT EXISTS extension_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    item_id TEXT,
    target_user_id TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_extension_logs_user ON extension_logs(user_id, created_at);

-- ============================================
-- RPC: Atomic invoice counter increment
-- ============================================
CREATE OR REPLACE FUNCTION increment_invoice_counter(p_user_id BIGINT)
RETURNS INTEGER AS $$
DECLARE
    new_counter INTEGER;
BEGIN
    UPDATE users SET invoice_counter = invoice_counter + 1 WHERE id = p_user_id
    RETURNING invoice_counter INTO new_counter;
    RETURN COALESCE(new_counter, 1);
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- RPC: Atomic referral count increment
-- ============================================
CREATE OR REPLACE FUNCTION increment_referral_count(p_user_id BIGINT)
RETURNS VOID AS $$
BEGIN
    UPDATE users SET referral_count = referral_count + 1 WHERE id = p_user_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- RPC: Get users with stats (avoids N+1)
-- ============================================
CREATE OR REPLACE FUNCTION get_users_with_stats()
RETURNS TABLE (
    id BIGINT, email TEXT, name TEXT, picture TEXT, monitoring_type TEXT,
    plan TEXT, billing_period TEXT, scan_frequency INTEGER, created_at TIMESTAMPTZ,
    gmail_count BIGINT, orders_count BIGINT, last_scan TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT u.id, u.email, u.name, u.picture, u.monitoring_type,
           u.plan, u.billing_period, u.scan_frequency, u.created_at,
           (SELECT COUNT(*) FROM gmail_accounts WHERE gmail_accounts.user_id = u.id) as gmail_count,
           (SELECT COUNT(*) FROM processed_orders
            WHERE processed_orders.user_id = u.id AND processed_orders.monitoring_type = u.monitoring_type) as orders_count,
           (SELECT sl.scanned_at FROM scan_logs sl
            WHERE sl.user_id = u.id AND sl.monitoring_type = u.monitoring_type
            ORDER BY sl.scanned_at DESC LIMIT 1) as last_scan
    FROM users u
    ORDER BY u.created_at DESC;
END;
$$ LANGUAGE plpgsql;
