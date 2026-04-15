-- ============================================
-- Migration: Add vinted_accounts table
-- Run this in the Supabase SQL Editor
-- ============================================

CREATE TABLE IF NOT EXISTS vinted_accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT DEFAULT '',
    refresh_token TEXT NOT NULL,
    vinted_user_id TEXT NOT NULL DEFAULT '',
    vinted_username TEXT DEFAULT '',
    domain TEXT DEFAULT 'fr',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vinted_accounts_user ON vinted_accounts(user_id);
