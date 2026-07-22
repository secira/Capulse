#!/usr/bin/env python3
"""
Railway Database Migration Script
Runs on every deployment to ensure the schema is fully up to date.

Strategy:
  1. verify_database_connection()  — quick connectivity check
  2. run_migrations()               — tries Alembic first, falls back to
                                     create_tables_directly() which calls
                                     db.create_all() (new tables) and then
                                     ensure_missing_columns() (ALTER TABLE
                                     ADD COLUMN IF NOT EXISTS for every
                                     column that may have been added after
                                     the initial table was created).
  3. seed_defaults()               — ensures default Tenant and AccountManager
                                     rows exist.
"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _fix_db_url(url: str) -> str:
    """Normalise Railway / Heroku postgres:// → postgresql+psycopg2://"""
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    if url.startswith('postgresql://') and '+psycopg2' not in url:
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    return url


def _col(session, ddl: str, label: str):
    """Execute a single ALTER TABLE … ADD COLUMN IF NOT EXISTS statement."""
    from sqlalchemy import text
    try:
        session.execute(text(ddl))
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("  [skip] %s: %s", label, exc)


# ─────────────────────────────────────────────
# Raw-SQL tables (not defined as SQLAlchemy models)
# ─────────────────────────────────────────────

def ensure_raw_tables(session):
    """Create tables that are defined via raw SQL (not SQLAlchemy models)."""
    from sqlalchemy import text
    logger.info("Creating raw-SQL tables if missing…")

    raw_tables = [
        ("""CREATE TABLE IF NOT EXISTS data_source_config (
            id SERIAL PRIMARY KEY,
            source_key VARCHAR(50) NOT NULL UNIQUE,
            display_name VARCHAR(100) NOT NULL,
            description TEXT,
            icon VARCHAR(50) DEFAULT 'fa-database',
            is_active BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )""", "data_source_config"),

        ("""CREATE TABLE IF NOT EXISTS fno_signal_history (
            id SERIAL PRIMARY KEY,
            index_id VARCHAR(20) DEFAULT 'NIFTY',
            signal_type VARCHAR(20) DEFAULT 'SCAN',
            direction VARCHAR(20),
            confidence INTEGER DEFAULT 0,
            confidence_grade VARCHAR(20),
            entry_mode VARCHAR(20),
            spot_price FLOAT,
            atm_strike INTEGER,
            trades_json TEXT,
            layers_json TEXT,
            alert_sent BOOLEAN DEFAULT FALSE,
            data_source VARCHAR(50) DEFAULT 'nse_python',
            trade_code VARCHAR(20),
            outcome VARCHAR(50),
            exit_spot FLOAT,
            exit_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )""", "fno_signal_history"),

        ("""CREATE TABLE IF NOT EXISTS fno_config (
            id              SERIAL PRIMARY KEY,
            telegram_fields TEXT         DEFAULT '',
            telegram_mode   VARCHAR(10)  DEFAULT 'teaser',
            updated_at      TIMESTAMP    DEFAULT NOW(),
            updated_by      VARCHAR(100)
        )""", "fno_config"),

        ("""CREATE TABLE IF NOT EXISTS data_api_broker (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            broker_type VARCHAR(50) NOT NULL,
            broker_name VARCHAR(100) NOT NULL,
            api_key TEXT,
            access_token TEXT,
            api_secret TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            connection_status VARCHAR(20) DEFAULT 'disconnected',
            last_connected TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""", "data_api_broker"),

        ("""CREATE TABLE IF NOT EXISTS data_api_plan (
            id SERIAL PRIMARY KEY,
            plan_type VARCHAR(30) NOT NULL DEFAULT 'user_data',
            truedata_api_key TEXT,
            truedata_api_secret TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT NOW(),
            updated_by VARCHAR(100)
        )""", "data_api_plan"),

        ("""CREATE TABLE IF NOT EXISTS behavioural_alerts (
            id SERIAL PRIMARY KEY,
            tenant_id VARCHAR(255) DEFAULT 'live',
            user_id INTEGER NOT NULL,
            alert_type VARCHAR(50) NOT NULL,
            severity VARCHAR(10) NOT NULL,
            title VARCHAR(200),
            description TEXT,
            advice TEXT,
            acknowledged BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            acknowledged_at TIMESTAMP
        )""", "behavioural_alerts"),

        # ── Trader Intelligence Profiling (Trader DNA, L1–L6) ────────────
        # Backs the /dashboard/trader-intelligence wizard, the result page
        # and the dashboard/profile level badge.
        #
        # NOTE: DDL MUST stay structurally identical to the `_always_create`
        # block in app.py (same FKs and ON DELETE CASCADE). CREATE TABLE
        # IF NOT EXISTS will NOT add missing constraints on a subsequent
        # boot, so any drift between the two paths becomes permanent.
        ("""CREATE TABLE IF NOT EXISTS trader_profile (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE REFERENCES "user"(id) ON DELETE CASCADE,
            tenant_id VARCHAR(255) DEFAULT 'live',
            trader_level VARCHAR(4) NOT NULL DEFAULT 'L1',
            overall_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            discipline_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            risk_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            emotional_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            strategy_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            experience_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            market_understanding_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            behavioural_risk VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
            xp_points INTEGER NOT NULL DEFAULT 0,
            completed_at TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""", "trader_profile"),
        ("CREATE INDEX IF NOT EXISTS ix_trader_profile_tenant ON trader_profile (tenant_id)",
         "ix_trader_profile_tenant"),

        ("""CREATE TABLE IF NOT EXISTS trader_answer (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            profile_id INTEGER REFERENCES trader_profile(id) ON DELETE CASCADE,
            question_id VARCHAR(8) NOT NULL,
            answer TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""", "trader_answer"),
        ("CREATE INDEX IF NOT EXISTS ix_trader_answer_user ON trader_answer (user_id)",
         "ix_trader_answer_user"),
        ("CREATE INDEX IF NOT EXISTS ix_trader_answer_profile ON trader_answer (profile_id)",
         "ix_trader_answer_profile"),

        ("""CREATE TABLE IF NOT EXISTS trader_progression (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            from_level VARCHAR(4),
            to_level VARCHAR(4) NOT NULL,
            overall_score DOUBLE PRECISION,
            xp_earned INTEGER NOT NULL DEFAULT 0,
            date_achieved TIMESTAMP DEFAULT NOW()
        )""", "trader_progression"),
        ("CREATE INDEX IF NOT EXISTS ix_trader_progression_user ON trader_progression (user_id)",
         "ix_trader_progression_user"),

        # ── Partner Network tables ───────────────────────────────────────────
        ("""CREATE TABLE IF NOT EXISTS partners (
            id VARCHAR(36) PRIMARY KEY,
            partner_display_id VARCHAR(20) UNIQUE,
            partner_code VARCHAR(30) UNIQUE NOT NULL,
            user_id INTEGER REFERENCES "user"(id),
            name VARCHAR(200) NOT NULL,
            mobile VARCHAR(15) NOT NULL,
            email VARCHAR(200) NOT NULL,
            partner_type VARCHAR(20) NOT NULL DEFAULT 'individual',
            commission_percentage NUMERIC(5,2) NOT NULL DEFAULT 20.00,
            pan_number VARCHAR(10),
            gst_number VARCHAR(15),
            kyc_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            bank_account_number VARCHAR(60),
            bank_ifsc VARCHAR(11),
            upi_id VARCHAR(100),
            wallet_balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            parent_partner_id VARCHAR(36) REFERENCES partners(id),
            admin_notes TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""", "partners"),

        ("""CREATE TABLE IF NOT EXISTS trader_referrals (
            id VARCHAR(36) PRIMARY KEY,
            trader_id INTEGER NOT NULL UNIQUE REFERENCES "user"(id),
            partner_id VARCHAR(36) NOT NULL REFERENCES partners(id),
            referral_code VARCHAR(30) NOT NULL,
            referral_locked BOOLEAN NOT NULL DEFAULT TRUE,
            linked_date TIMESTAMP DEFAULT NOW(),
            attribution_source VARCHAR(30) DEFAULT 'signup_code'
        )""", "trader_referrals"),
        ("CREATE INDEX IF NOT EXISTS ix_trader_referrals_partner ON trader_referrals (partner_id)",
         "ix_trader_referrals_partner"),

        ("""CREATE TABLE IF NOT EXISTS partner_commissions (
            id VARCHAR(36) PRIMARY KEY,
            partner_id VARCHAR(36) NOT NULL REFERENCES partners(id),
            trader_id INTEGER NOT NULL REFERENCES "user"(id),
            subscription_id VARCHAR(100),
            gross_amount NUMERIC(10,2) NOT NULL,
            gateway_fee NUMERIC(10,2) NOT NULL DEFAULT 0,
            net_amount NUMERIC(10,2) NOT NULL,
            commission_percent NUMERIC(5,2) NOT NULL,
            commission_amount NUMERIC(10,2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending_hold',
            hold_until TIMESTAMP,
            plan_type VARCHAR(30),
            created_at TIMESTAMP DEFAULT NOW(),
            approved_at TIMESTAMP,
            paid_at TIMESTAMP,
            clawback_reason VARCHAR(255)
        )""", "partner_commissions"),
        ("CREATE INDEX IF NOT EXISTS ix_partner_commissions_partner ON partner_commissions (partner_id)",
         "ix_partner_commissions_partner"),
        ("CREATE INDEX IF NOT EXISTS ix_partner_commissions_status ON partner_commissions (status, hold_until)",
         "ix_partner_commissions_status"),

        ("""CREATE TABLE IF NOT EXISTS payout_requests (
            id VARCHAR(36) PRIMARY KEY,
            partner_id VARCHAR(36) NOT NULL REFERENCES partners(id),
            amount NUMERIC(10,2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'requested',
            requested_at TIMESTAMP DEFAULT NOW(),
            reviewed_at TIMESTAMP,
            paid_at TIMESTAMP,
            payment_reference VARCHAR(100),
            rejection_reason VARCHAR(255),
            reviewed_by_note VARCHAR(255)
        )""", "payout_requests"),
        ("CREATE INDEX IF NOT EXISTS ix_payout_requests_partner ON payout_requests (partner_id)",
         "ix_payout_requests_partner"),

        ("""CREATE TABLE IF NOT EXISTS broker_details_partner (
            id VARCHAR(36) PRIMARY KEY,
            broker_partner_id VARCHAR(36) NOT NULL REFERENCES partners(id),
            broker_code VARCHAR(20) NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )""", "broker_details_partner"),
    ]

    for ddl, label in raw_tables:
        try:
            session.execute(text(ddl))
            session.commit()
            logger.info("  Table %s ready.", label)
        except Exception as exc:
            session.rollback()
            logger.warning("  [skip] %s: %s", label, exc)

    seed_data_sources = [
        ("INSERT INTO data_source_config (source_key, display_name, description, icon, is_active) "
         "VALUES ('nse_python', 'NSE Python (Default)', 'Uses NSEPython, yfinance, and NSE official API for option chain and market data. Free, no API key required.', 'fa-code', true) "
         "ON CONFLICT (source_key) DO NOTHING", "seed nse_python"),
        ("INSERT INTO data_source_config (source_key, display_name, description, icon, is_active) "
         "VALUES ('truedata', 'TrueData API', 'Professional real-time data feed with sub-second latency. Requires TrueData subscription and API key.', 'fa-bolt', false) "
         "ON CONFLICT (source_key) DO NOTHING", "seed truedata"),
        ("INSERT INTO data_source_config (source_key, display_name, description, icon, is_active) "
         "VALUES ('user_custom', 'User Data Source', 'Manual CSV upload or custom data input for backtesting and historical analysis.', 'fa-upload', false) "
         "ON CONFLICT (source_key) DO NOTHING", "seed user_custom"),
    ]
    for ddl, label in seed_data_sources:
        _col(session, ddl, label)

    try:
        session.execute(text(
            "INSERT INTO data_api_plan (plan_type, is_active) "
            "SELECT 'user_data', true "
            "WHERE NOT EXISTS (SELECT 1 FROM data_api_plan)"
        ))
        session.commit()
        logger.info("  Seeded default data_api_plan.")
    except Exception as exc:
        session.rollback()
        logger.warning("  [skip] seed data_api_plan: %s", exc)

    logger.info("Raw table creation complete.")


# ─────────────────────────────────────────────
# Column-level migrations (ADD COLUMN IF NOT EXISTS)
# ─────────────────────────────────────────────

def ensure_missing_columns(session):
    """
    Add every column that may be missing in an existing Railway database.
    Safe to run on a fresh database (IF NOT EXISTS means no-ops).
    """
    logger.info("Ensuring all columns exist (ADD COLUMN IF NOT EXISTS)…")

    # ── user ──────────────────────────────────────────────────
    cols = [
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                      "user.tenant_id"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS mobile_number VARCHAR(20)",                                                   "user.mobile_number"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS mobile_verified BOOLEAN DEFAULT FALSE",                                       "user.mobile_verified"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS current_otp VARCHAR(10)",                                                     "user.current_otp"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS otp_expires_at TIMESTAMP",                                                    "user.otp_expires_at"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS otp_attempts INTEGER DEFAULT 0",                                              "user.otp_attempts"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS last_otp_request TIMESTAMP",                                                  "user.last_otp_request"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS profile_image_url VARCHAR(500)",                                              "user.profile_image_url"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT 'en'",                                 "user.preferred_language"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS pricing_plan VARCHAR(20) DEFAULT 'FREE'",                                     "user.pricing_plan"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(20) DEFAULT 'INACTIVE'",                          "user.subscription_status"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_start_date TIMESTAMP",                                           "user.subscription_start_date"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_end_date TIMESTAMP",                                             "user.subscription_end_date"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP",                                           "user.subscription_expires_at"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS razorpay_customer_id VARCHAR(100)",                                           "user.razorpay_customer_id"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS razorpay_subscription_id VARCHAR(100)",                                       "user.razorpay_subscription_id"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS billing_cycle VARCHAR(20) DEFAULT 'monthly'",                                 "user.billing_cycle"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS total_payments FLOAT DEFAULT 0.0",                                            "user.total_payments"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20)",                                                   "user.referral_code"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS referred_by INTEGER",                                                         "user.referred_by"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS first_name VARCHAR(50)",                                                      "user.first_name"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS last_name VARCHAR(50)",                                                       "user.last_name"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE",                                                 "user.active"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE",                                              "user.is_admin"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE",                                           "user.is_verified"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN DEFAULT FALSE",                                    "user.two_factor_enabled"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",                                                        "user.last_login"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS trial_extended_until TIMESTAMP",                                              "user.trial_extended_until"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS trial_extended_at TIMESTAMP",                                                 "user.trial_extended_at"),
        ("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS use_remote_execution BOOLEAN NOT NULL DEFAULT FALSE",                         "user.use_remote_execution"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── user_brokers ──────────────────────────────────────────
    cols = [
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                  "user_brokers.tenant_id"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS broker_name VARCHAR(50)",                                                 "user_brokers.broker_name"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS api_key TEXT",                                                            "user_brokers.api_key"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS access_token TEXT",                                                       "user_brokers.access_token"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS api_secret TEXT",                                                         "user_brokers.api_secret"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS connection_status VARCHAR(20) DEFAULT 'disconnected'",                    "user_brokers.connection_status"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS is_primary BOOLEAN DEFAULT FALSE",                                        "user_brokers.is_primary"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS last_connected TIMESTAMP",                                                "user_brokers.last_connected"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS account_balance FLOAT DEFAULT 0.0",                                       "user_brokers.account_balance"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS margin_available FLOAT DEFAULT 0.0",                                      "user_brokers.margin_available"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                          "user_brokers.is_active"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS request_token TEXT",                                                      "user_brokers.request_token"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS redirect_url TEXT",                                                       "user_brokers.redirect_url"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS last_token_refresh TIMESTAMP",                                            "user_brokers.last_token_refresh"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS last_sync TIMESTAMP",                                                     "user_brokers.last_sync"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                      "user_brokers.updated_at"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS sync_status VARCHAR(20) DEFAULT 'pending'",                                  "user_brokers.sync_status"),
        ("ALTER TABLE user_brokers ADD COLUMN IF NOT EXISTS is_data_broker BOOLEAN DEFAULT FALSE",                                         "user_brokers.is_data_broker"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── portfolio ──────────────────────────────────────────────
    cols = [
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                     "portfolio.tenant_id"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS broker_account_id INTEGER",                                                  "portfolio.broker_account_id"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS broker_id INTEGER",                                                          "portfolio.broker_id"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS ticker_symbol VARCHAR(50)",                                                   "portfolio.ticker_symbol"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS stock_name VARCHAR(200)",                                                    "portfolio.stock_name"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS asset_type VARCHAR(50) DEFAULT 'stocks'",                                   "portfolio.asset_type"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS asset_category VARCHAR(50)",                                                 "portfolio.asset_category"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS exchange VARCHAR(20)",                                                       "portfolio.exchange"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS isin VARCHAR(20)",                                                           "portfolio.isin"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS contract_type VARCHAR(20)",                                                  "portfolio.contract_type"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS expiry_date DATE",                                                           "portfolio.expiry_date"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS lot_size INTEGER",                                                           "portfolio.lot_size"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS option_type VARCHAR(10)",                                                    "portfolio.option_type"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS nps_scheme VARCHAR(100)",                                                    "portfolio.nps_scheme"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pension_fund_manager VARCHAR(100)",                                          "portfolio.pension_fund_manager"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS tier VARCHAR(10)",                                                           "portfolio.tier"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS property_type VARCHAR(50)",                                                  "portfolio.property_type"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS property_location VARCHAR(200)",                                             "portfolio.property_location"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS area_sqft FLOAT",                                                            "portfolio.area_sqft"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS interest_rate FLOAT",                                                        "portfolio.interest_rate"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS gold_form VARCHAR(50)",                                                      "portfolio.gold_form"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS gold_purity VARCHAR(20)",                                                    "portfolio.gold_purity"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS grams FLOAT",                                                                "portfolio.grams"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS folio_number VARCHAR(50)",                                                   "portfolio.folio_number"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS nav FLOAT",                                                                  "portfolio.nav"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS units FLOAT",                                                                "portfolio.units"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS face_value FLOAT",                                                           "portfolio.face_value"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS coupon_rate FLOAT",                                                          "portfolio.coupon_rate"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS maturity_date DATE",                                                         "portfolio.maturity_date"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS date_purchased DATE",                                                        "portfolio.date_purchased"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS purchase_price FLOAT",                                                       "portfolio.purchase_price"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS purchased_value FLOAT",                                                      "portfolio.purchased_value"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS current_value FLOAT",                                                        "portfolio.current_value"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS market_value FLOAT",                                                         "portfolio.market_value"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS unrealized_pnl FLOAT",                                                       "portfolio.unrealized_pnl"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS unrealized_pnl_pct FLOAT",                                                   "portfolio.unrealized_pnl_pct"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS day_change FLOAT DEFAULT 0.0",                                               "portfolio.day_change"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS day_change_pct FLOAT DEFAULT 0.0",                                           "portfolio.day_change_pct"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS trade_type VARCHAR(20)",                                                     "portfolio.trade_type"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS data_source VARCHAR(50) DEFAULT 'manual'",                                   "portfolio.data_source"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS last_sync_date TIMESTAMP",                                                   "portfolio.last_sync_date"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP",                                                   "portfolio.last_synced_at"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                             "portfolio.is_active"),
        ("ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                         "portfolio.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── research_list ─────────────────────────────────────────
    cols = [
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                 "research_list.tenant_id"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS asset_type VARCHAR(30) DEFAULT 'stocks'",                               "research_list.asset_type"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS sector VARCHAR(100)",                                                    "research_list.sector"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS i_score NUMERIC(5,2)",                                                   "research_list.i_score"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS recommendation VARCHAR(30)",                                             "research_list.recommendation"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS confidence NUMERIC(5,2)",                                                "research_list.confidence"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS qualitative_score NUMERIC(5,2)",                                         "research_list.qualitative_score"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS quantitative_score NUMERIC(5,2)",                                        "research_list.quantitative_score"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS search_score NUMERIC(5,2)",                                              "research_list.search_score"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS trend_score NUMERIC(5,2)",                                               "research_list.trend_score"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS qualitative_details JSONB",                                              "research_list.qualitative_details"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS quantitative_details JSONB",                                             "research_list.quantitative_details"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS search_details JSONB",                                                   "research_list.search_details"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS trend_details JSONB",                                                    "research_list.trend_details"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS current_price NUMERIC(12,2)",                                            "research_list.current_price"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS previous_close NUMERIC(12,2)",                                           "research_list.previous_close"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS price_change_pct NUMERIC(8,4)",                                          "research_list.price_change_pct"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS future_parameters JSONB",                                                "research_list.future_parameters"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS recommendation_summary TEXT",                                            "research_list.recommendation_summary"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                         "research_list.is_active"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS last_computed_at TIMESTAMP",                                             "research_list.last_computed_at"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS last_requested_at TIMESTAMP",                                            "research_list.last_requested_at"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS computation_source VARCHAR(50) DEFAULT 'nightly'",                       "research_list.computation_source"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                     "research_list.updated_at"),
        ("ALTER TABLE research_list ADD COLUMN IF NOT EXISTS hist_data_source VARCHAR(50)",                                               "research_list.hist_data_source"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── research_weight_config ────────────────────────────────
    cols = [
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                        "research_weight_config.tenant_id"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS qualitative_pct INTEGER DEFAULT 15",                           "research_weight_config.qualitative_pct"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS quantitative_pct INTEGER DEFAULT 30",                          "research_weight_config.quantitative_pct"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS search_pct INTEGER DEFAULT 10",                                "research_weight_config.search_pct"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS trend_pct INTEGER DEFAULT 20",                                 "research_weight_config.trend_pct"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS risk_pct INTEGER DEFAULT 20",                                  "research_weight_config.risk_pct"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS market_context_pct INTEGER DEFAULT 5",                         "research_weight_config.market_context_pct"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS tech_params JSONB",                                            "research_weight_config.tech_params"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS trend_params JSONB",                                           "research_weight_config.trend_params"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS qualitative_sources JSONB",                                    "research_weight_config.qualitative_sources"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS research_flags JSONB",                                         "research_weight_config.research_flags"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS portfolio_flags JSONB",                                        "research_weight_config.portfolio_flags"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1",                                    "research_weight_config.version"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                               "research_weight_config.is_active"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS effective_from TIMESTAMP",                                     "research_weight_config.effective_from"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS created_by INTEGER",                                           "research_weight_config.created_by"),
        ("ALTER TABLE research_weight_config ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",                           "research_weight_config.created_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── research_threshold_config ─────────────────────────────
    cols = [
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                     "research_threshold_config.tenant_id"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS strong_buy_threshold FLOAT DEFAULT 78",                     "research_threshold_config.strong_buy_threshold"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS buy_threshold FLOAT DEFAULT 63",                            "research_threshold_config.buy_threshold"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS hold_low FLOAT DEFAULT 42",                                 "research_threshold_config.hold_low"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS hold_high FLOAT DEFAULT 62",                                "research_threshold_config.hold_high"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS sell_threshold FLOAT DEFAULT 28",                           "research_threshold_config.sell_threshold"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS min_confidence FLOAT DEFAULT 0.45",                         "research_threshold_config.min_confidence"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                            "research_threshold_config.is_active"),
        ("ALTER TABLE research_threshold_config ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",                        "research_threshold_config.created_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── research_run ──────────────────────────────────────────
    cols = [
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                  "research_run.tenant_id"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS asset_name VARCHAR(200)",                                                 "research_run.asset_name"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS date_range_start DATE",                                                   "research_run.date_range_start"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS date_range_end DATE",                                                     "research_run.date_range_end"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS error_message TEXT",                                                      "research_run.error_message"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS confidence NUMERIC(3,2)",                                                 "research_run.confidence"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS recommendation_summary TEXT",                                             "research_run.recommendation_summary"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS inputs_json JSONB",                                                       "research_run.inputs_json"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS cache_key VARCHAR(64)",                                                   "research_run.cache_key"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS run_started_at TIMESTAMP",                                                "research_run.run_started_at"),
        ("ALTER TABLE research_run ADD COLUMN IF NOT EXISTS run_completed_at TIMESTAMP",                                              "research_run.run_completed_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── research_cache ────────────────────────────────────────
    cols = [
        ("ALTER TABLE research_cache ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                "research_cache.tenant_id"),
        ("ALTER TABLE research_cache ADD COLUMN IF NOT EXISTS overall_score NUMERIC(5,2)",                                            "research_cache.overall_score"),
        ("ALTER TABLE research_cache ADD COLUMN IF NOT EXISTS recommendation VARCHAR(30)",                                            "research_cache.recommendation"),
        ("ALTER TABLE research_cache ADD COLUMN IF NOT EXISTS is_valid BOOLEAN DEFAULT TRUE",                                         "research_cache.is_valid"),
        ("ALTER TABLE research_cache ADD COLUMN IF NOT EXISTS hit_count INTEGER DEFAULT 0",                                           "research_cache.hit_count"),
        ("ALTER TABLE research_cache ADD COLUMN IF NOT EXISTS last_hit_at TIMESTAMP",                                                 "research_cache.last_hit_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── tenants ───────────────────────────────────────────────
    cols = [
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS slug VARCHAR(100)",                                                           "tenants.slug"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS domain VARCHAR(255)",                                                          "tenants.domain"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500)",                                                        "tenants.logo_url"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS primary_color VARCHAR(20)",                                                    "tenants.primary_color"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS secondary_color VARCHAR(20)",                                                  "tenants.secondary_color"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS features JSONB",                                                              "tenants.features"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS contact_email VARCHAR(120)",                                                   "tenants.contact_email"),
        ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(20)",                                                    "tenants.contact_phone"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── trading_signals (LangGraphSignal) ─────────────────────
    cols = [
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                "trading_signal.tenant_id"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS timeframe VARCHAR(20)",                                                "trading_signal.timeframe"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS rationale TEXT",                                                       "trading_signal.rationale"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS pipeline_metadata JSONB",                                              "trading_signal.pipeline_metadata"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS signal_data JSONB",                                                     "trading_signal.signal_data"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS pipeline_state JSONB",                                                  "trading_signal.pipeline_state"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS error_message TEXT",                                                    "trading_signal.error_message"),
        ("ALTER TABLE trading_signal ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",                                                "trading_signal.completed_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── daily_trading_signals ────────────────────────────────
    cols = [
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                         "daily_trading_signals.tenant_id"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS strike_price NUMERIC(12,2)",                                     "daily_trading_signals.strike_price"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS strike_type VARCHAR(10)",                                        "daily_trading_signals.strike_type"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(100) DEFAULT 'Trend Following'",           "daily_trading_signals.strategy_name"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS target_3 NUMERIC(12,2)",                                         "daily_trading_signals.target_3"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS profit_points NUMERIC(12,2) DEFAULT 0",                          "daily_trading_signals.profit_points"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS loss_points NUMERIC(12,2) DEFAULT 0",                            "daily_trading_signals.loss_points"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS final_points NUMERIC(12,2) DEFAULT 0",                           "daily_trading_signals.final_points"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS trade_outcome VARCHAR(50)",                                      "daily_trading_signals.trade_outcome"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS risk_level VARCHAR(10) DEFAULT 'MEDIUM'",                        "daily_trading_signals.risk_level"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS analyst_name VARCHAR(100)",                                      "daily_trading_signals.analyst_name"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS shared_whatsapp BOOLEAN DEFAULT FALSE",                          "daily_trading_signals.shared_whatsapp"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS shared_telegram BOOLEAN DEFAULT FALSE",                          "daily_trading_signals.shared_telegram"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS whatsapp_shared_at TIMESTAMP",                                   "daily_trading_signals.whatsapp_shared_at"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS telegram_shared_at TIMESTAMP",                                   "daily_trading_signals.telegram_shared_at"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP",                                            "daily_trading_signals.closed_at"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS call_time TIMESTAMP",                                            "daily_trading_signals.call_time"),
        ("ALTER TABLE daily_trading_signals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                             "daily_trading_signals.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── fno_signal_history (ADX gate additions) ────────────────────────
    cols = [
        ("ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS market_regime VARCHAR(20)",                                         "fno_signal_history.market_regime"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── trading_signals (TradingSignal model) ────────────────
    cols = [
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                               "trading_signals.tenant_id"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS signal_strength VARCHAR(20)",                                          "trading_signals.signal_strength"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS entry_price FLOAT",                                                    "trading_signals.entry_price"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS stop_loss FLOAT",                                                      "trading_signals.stop_loss"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS target_price FLOAT",                                                   "trading_signals.target_price"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS risk_reward_ratio FLOAT",                                              "trading_signals.risk_reward_ratio"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS technical_data JSONB",                                                 "trading_signals.technical_data"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS ai_reasoning TEXT",                                                    "trading_signals.ai_reasoning"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                       "trading_signals.is_active"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",                                                 "trading_signals.expires_at"),
        ("ALTER TABLE trading_signals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                   "trading_signals.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── blog_post ─────────────────────────────────────────────
    cols = [
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS author_id INTEGER",                                                          "blog_post.author_id"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS excerpt VARCHAR(300)",                                                       "blog_post.excerpt"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS featured_image VARCHAR(500)",                                                "blog_post.featured_image"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS category VARCHAR(50)",                                                       "blog_post.category"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS tags VARCHAR(200)",                                                          "blog_post.tags"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS meta_description VARCHAR(160)",                                              "blog_post.meta_description"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS published_at TIMESTAMP",                                                     "blog_post.published_at"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE",                                          "blog_post.is_featured"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0",                                               "blog_post.view_count"),
        ("ALTER TABLE blog_post ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                         "blog_post.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── risk_profiles ─────────────────────────────────────────
    cols = [
        ("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                 "risk_profiles.tenant_id"),
        ("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS investment_horizon VARCHAR(20)",                                         "risk_profiles.investment_horizon"),
        ("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS monthly_income FLOAT",                                                   "risk_profiles.monthly_income"),
        ("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS existing_investments FLOAT",                                             "risk_profiles.existing_investments"),
        ("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS investment_goals JSONB",                                                 "risk_profiles.investment_goals"),
        ("ALTER TABLE risk_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                     "risk_profiles.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── admins ────────────────────────────────────────────────
    cols = [
        ("ALTER TABLE admins ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                        "admins.tenant_id"),
        ("ALTER TABLE admins ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN DEFAULT FALSE",                                         "admins.is_super_admin"),
        ("ALTER TABLE admins ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",                                                         "admins.last_login"),
        ("ALTER TABLE admins ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                               "admins.is_active"),
        ("ALTER TABLE admins ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                            "admins.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── portfolio_preferences ─────────────────────────────────
    cols = [
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                         "portfolio_preferences.tenant_id"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS goal_amount FLOAT",                                              "portfolio_preferences.goal_amount"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS goal_timeline_months INTEGER",                                   "portfolio_preferences.goal_timeline_months"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS monthly_sip_amount FLOAT",                                       "portfolio_preferences.monthly_sip_amount"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS preferred_brokers JSONB",                                        "portfolio_preferences.preferred_brokers"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS excluded_sectors JSONB",                                         "portfolio_preferences.excluded_sectors"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS preferred_asset_classes JSONB",                                  "portfolio_preferences.preferred_asset_classes"),
        ("ALTER TABLE portfolio_preferences ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                             "portfolio_preferences.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── account_managers ──────────────────────────────────────
    cols = [
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS title VARCHAR(100) DEFAULT 'Account Manager'",                       "account_managers.title"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS whatsapp VARCHAR(20)",                                               "account_managers.whatsapp"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS experience_years INTEGER",                                            "account_managers.experience_years"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS success_rate NUMERIC(5,2)",                                          "account_managers.success_rate"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS average_return NUMERIC(5,2)",                                        "account_managers.average_return"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS risk_management VARCHAR(50)",                                        "account_managers.risk_management"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS avatar_initials VARCHAR(5)",                                         "account_managers.avatar_initials"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500)",                                            "account_managers.avatar_url"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS avatar_color VARCHAR(20) DEFAULT '#4299e1'",                         "account_managers.avatar_color"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS working_hours VARCHAR(100)",                                         "account_managers.working_hours"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE",                                   "account_managers.is_default"),
        ("ALTER TABLE account_managers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                  "account_managers.updated_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── broker_holdings ───────────────────────────────────────
    cols = [
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                               "broker_holdings.tenant_id"),
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS security_id VARCHAR(20)",                                              "broker_holdings.security_id"),
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS isin VARCHAR(20)",                                                     "broker_holdings.isin"),
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS dp_quantity INTEGER DEFAULT 0",                                        "broker_holdings.dp_quantity"),
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS collateral_quantity INTEGER DEFAULT 0",                                "broker_holdings.collateral_quantity"),
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS last_trade_price FLOAT DEFAULT 0.0",                                   "broker_holdings.last_trade_price"),
        ("ALTER TABLE broker_holdings ADD COLUMN IF NOT EXISTS investment_value FLOAT DEFAULT 0.0",                                   "broker_holdings.investment_value"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── workflow_executions ───────────────────────────────────
    cols = [
        ("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS connector_type VARCHAR(50)",                                       "workflow_executions.connector_type"),
        ("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS connector_id VARCHAR(100)",                                        "workflow_executions.connector_id"),
        ("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS model_used VARCHAR(50)",                                           "workflow_executions.model_used"),
        ("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0",                                   "workflow_executions.total_tokens"),
        ("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS total_cost_usd FLOAT DEFAULT 0.0",                                 "workflow_executions.total_cost_usd"),
        ("ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS duration_ms INTEGER",                                              "workflow_executions.duration_ms"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── manual_trade_imports ──────────────────────────────────
    cols = [
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                          "manual_trade_imports.tenant_id"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS asset_type VARCHAR(20) DEFAULT 'STOCK'",                         "manual_trade_imports.asset_type"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS instrument_detail VARCHAR(100) DEFAULT ''",                      "manual_trade_imports.instrument_detail"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS pnl_percentage FLOAT DEFAULT 0.0",                               "manual_trade_imports.pnl_percentage"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS holding_period_hours FLOAT DEFAULT 0.0",                         "manual_trade_imports.holding_period_hours"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS total_charges FLOAT DEFAULT 0.0",                                "manual_trade_imports.total_charges"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS net_pnl FLOAT DEFAULT 0.0",                                      "manual_trade_imports.net_pnl"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'csv_upload'",                        "manual_trade_imports.source"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(100) DEFAULT 'Manual Import'",             "manual_trade_imports.strategy_name"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(20) DEFAULT 'MANUAL'",                       "manual_trade_imports.exit_reason"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS broker_name VARCHAR(50) DEFAULT 'Manual'",                       "manual_trade_imports.broker_name"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS external_trade_id VARCHAR(100)",                                   "manual_trade_imports.external_trade_id"),
        ("ALTER TABLE manual_trade_imports ADD COLUMN IF NOT EXISTS transaction_type VARCHAR(10)",                                     "manual_trade_imports.transaction_type"),
        ("CREATE INDEX IF NOT EXISTS ix_manual_trade_imports_external_trade_id ON manual_trade_imports (external_trade_id)",           "manual_trade_imports.ix_external_trade_id"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── manual_mutual_fund_holdings ────────────────────────────
    cols = [
        ("ALTER TABLE manual_mutual_fund_holdings ADD COLUMN IF NOT EXISTS platform_name VARCHAR(100)",                               "manual_mutual_fund_holdings.platform_name"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── manual_commodity_holdings ──────────────────────────────
    cols = [
        ("ALTER TABLE manual_commodity_holdings ADD COLUMN IF NOT EXISTS platform_name VARCHAR(100)",                                 "manual_commodity_holdings.platform_name"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── market_analysis ────────────────────────────────────────
    cols = [
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                               "market_analysis.tenant_id"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS ema_signal VARCHAR(10)",                                               "market_analysis.ema_signal"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS rsi_value FLOAT",                                                     "market_analysis.rsi_value"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS macd_signal VARCHAR(10)",                                              "market_analysis.macd_signal"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS supertrend_signal VARCHAR(10)",                                        "market_analysis.supertrend_signal"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS support_level FLOAT",                                                  "market_analysis.support_level"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS resistance_level FLOAT",                                               "market_analysis.resistance_level"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS pivot_point FLOAT",                                                    "market_analysis.pivot_point"),
        ("ALTER TABLE market_analysis ADD COLUMN IF NOT EXISTS recommended_strategies TEXT",                                          "market_analysis.recommended_strategies"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── chat_conversations / chat_messages ────────────────────
    cols = [
        ("ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                            "chat_conversations.tenant_id"),
        ("ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS title VARCHAR(200)",                                                "chat_conversations.title"),
        ("ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                   "chat_conversations.is_active"),
        ("ALTER TABLE chat_conversations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                "chat_conversations.updated_at"),
        ("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                 "chat_messages.tenant_id"),
        ("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) DEFAULT 'text'",                               "chat_messages.message_type"),
        ("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS metadata_json JSONB",                                                    "chat_messages.metadata_json"),
        ("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE",                                         "chat_messages.is_read"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── research_conversations / research_messages ────────────
    cols = [
        ("ALTER TABLE research_conversations ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                        "research_conversations.tenant_id"),
        ("ALTER TABLE research_conversations ADD COLUMN IF NOT EXISTS asset_type VARCHAR(50)",                                        "research_conversations.asset_type"),
        ("ALTER TABLE research_conversations ADD COLUMN IF NOT EXISTS symbol VARCHAR(50)",                                            "research_conversations.symbol"),
        ("ALTER TABLE research_conversations ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                               "research_conversations.is_active"),
        ("ALTER TABLE research_messages ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                             "research_messages.tenant_id"),
        ("ALTER TABLE research_messages ADD COLUMN IF NOT EXISTS metadata_json JSONB",                                                "research_messages.metadata_json"),
        ("ALTER TABLE research_messages ADD COLUMN IF NOT EXISTS sources JSONB",                                                      "research_messages.sources"),
        ("ALTER TABLE research_messages ADD COLUMN IF NOT EXISTS tokens_used INTEGER DEFAULT 0",                                     "research_messages.tokens_used"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── vector_documents / source_citations ──────────────────
    cols = [
        ("ALTER TABLE vector_documents ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                              "vector_documents.tenant_id"),
        ("ALTER TABLE vector_documents ADD COLUMN IF NOT EXISTS document_type VARCHAR(50)",                                          "vector_documents.document_type"),
        ("ALTER TABLE vector_documents ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(50)",                                        "vector_documents.embedding_model"),
        ("ALTER TABLE vector_documents ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                     "vector_documents.is_active"),
        ("ALTER TABLE source_citations ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                              "source_citations.tenant_id"),
        ("ALTER TABLE source_citations ADD COLUMN IF NOT EXISTS relevance_score FLOAT",                                               "source_citations.relevance_score"),
        ("ALTER TABLE source_citations ADD COLUMN IF NOT EXISTS cited_at TIMESTAMP DEFAULT now()",                                   "source_citations.cited_at"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── trade_recommendations / active_trades / trade_history ─
    cols = [
        ("ALTER TABLE trade_recommendations ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                         "trade_recommendations.tenant_id"),
        ("ALTER TABLE trade_recommendations ADD COLUMN IF NOT EXISTS signal_source VARCHAR(50)",                                     "trade_recommendations.signal_source"),
        ("ALTER TABLE trade_recommendations ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",                                "trade_recommendations.is_active"),
        ("ALTER TABLE active_trades ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                 "active_trades.tenant_id"),
        ("ALTER TABLE active_trades ADD COLUMN IF NOT EXISTS broker_account_id INTEGER",                                             "active_trades.broker_account_id"),
        ("ALTER TABLE active_trades ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(50)",                                               "active_trades.exit_reason"),
        ("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                 "trade_history.tenant_id"),
        ("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS broker_account_id INTEGER",                                             "trade_history.broker_account_id"),
        ("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(50)",                                               "trade_history.exit_reason"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── payment / user_payments ───────────────────────────────
    cols = [
        ("ALTER TABLE payment ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                       "payment.tenant_id"),
        ("ALTER TABLE payment ADD COLUMN IF NOT EXISTS razorpay_payment_id VARCHAR(100)",                                             "payment.razorpay_payment_id"),
        ("ALTER TABLE payment ADD COLUMN IF NOT EXISTS razorpay_order_id VARCHAR(100)",                                               "payment.razorpay_order_id"),
        ("ALTER TABLE payment ADD COLUMN IF NOT EXISTS razorpay_signature VARCHAR(200)",                                              "payment.razorpay_signature"),
        ("ALTER TABLE payment ADD COLUMN IF NOT EXISTS failure_reason TEXT",                                                          "payment.failure_reason"),
        ("ALTER TABLE payment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",                                           "payment.updated_at"),
        ("ALTER TABLE user_payments ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'live'",                                  "user_payments.tenant_id"),
        ("ALTER TABLE user_payments ADD COLUMN IF NOT EXISTS gateway_response JSONB",                                                 "user_payments.gateway_response"),
        ("ALTER TABLE user_payments ADD COLUMN IF NOT EXISTS failure_reason TEXT",                                                    "user_payments.failure_reason"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── site_config ───────────────────────────────────────────
    _col(session,
         "ALTER TABLE site_config ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()",
         "site_config.updated_at")

    # ── portfolio_events ──────────────────────────────────────
    _col(session,
         "ALTER TABLE portfolio_events ADD COLUMN IF NOT EXISTS event_type VARCHAR(50)",
         "portfolio_events.event_type")
    _col(session,
         "ALTER TABLE portfolio_events ADD COLUMN IF NOT EXISTS symbol VARCHAR(50)",
         "portfolio_events.symbol")
    _col(session,
         "ALTER TABLE portfolio_events ADD COLUMN IF NOT EXISTS amount FLOAT",
         "portfolio_events.amount")

    # ── fno_signal_history ────────────────────────────────────
    # Columns added for multi-index support and trade-code / outcome tracking
    cols = [
        ("ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS index_id VARCHAR(20) DEFAULT 'NIFTY'",  "fno_signal_history.index_id"),
        ("ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS trade_code VARCHAR(20)",                 "fno_signal_history.trade_code"),
        ("ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS outcome VARCHAR(50)",                    "fno_signal_history.outcome"),
        ("ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS exit_spot FLOAT",                        "fno_signal_history.exit_spot"),
        ("ALTER TABLE fno_signal_history ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP",                    "fno_signal_history.exit_time"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # ── fno_config ────────────────────────────────────────────
    # Per-index SL/Target points, Telegram flags, and telegram_mode.
    # Defaults here use the new wider 1:2 R:R values (SL 15%, T1 30%):
    #   NIFTY/FINNIFTY: SL=30, T1=60, T2=90,  T3=120
    #   BANKNIFTY/SENSEX: SL=60, T1=120, T2=180, T3=240
    cols = [
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS nifty_sl_points FLOAT DEFAULT 30.0",            "fno_config.nifty_sl_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS nifty_target_points FLOAT DEFAULT 60.0",        "fno_config.nifty_target_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS nifty_target_2_points FLOAT DEFAULT 90.0",      "fno_config.nifty_target_2_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS nifty_target_3_points FLOAT DEFAULT 120.0",     "fno_config.nifty_target_3_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS nifty_telegram BOOLEAN DEFAULT TRUE",           "fno_config.nifty_telegram"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS banknifty_sl_points FLOAT DEFAULT 60.0",        "fno_config.banknifty_sl_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS banknifty_target_points FLOAT DEFAULT 120.0",   "fno_config.banknifty_target_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS banknifty_target_2_points FLOAT DEFAULT 180.0", "fno_config.banknifty_target_2_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS banknifty_target_3_points FLOAT DEFAULT 240.0", "fno_config.banknifty_target_3_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS banknifty_telegram BOOLEAN DEFAULT FALSE",      "fno_config.banknifty_telegram"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS finnifty_sl_points FLOAT DEFAULT 30.0",         "fno_config.finnifty_sl_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS finnifty_target_points FLOAT DEFAULT 60.0",     "fno_config.finnifty_target_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS finnifty_target_2_points FLOAT DEFAULT 90.0",   "fno_config.finnifty_target_2_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS finnifty_target_3_points FLOAT DEFAULT 120.0",  "fno_config.finnifty_target_3_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS finnifty_telegram BOOLEAN DEFAULT FALSE",       "fno_config.finnifty_telegram"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS sensex_sl_points FLOAT DEFAULT 60.0",          "fno_config.sensex_sl_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS sensex_target_points FLOAT DEFAULT 120.0",     "fno_config.sensex_target_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS sensex_target_2_points FLOAT DEFAULT 180.0",   "fno_config.sensex_target_2_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS sensex_target_3_points FLOAT DEFAULT 240.0",   "fno_config.sensex_target_3_points"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS sensex_telegram BOOLEAN DEFAULT FALSE",        "fno_config.sensex_telegram"),
        ("ALTER TABLE fno_config ADD COLUMN IF NOT EXISTS telegram_mode VARCHAR(10) DEFAULT 'teaser'",   "fno_config.telegram_mode"),
    ]
    for ddl, label in cols:
        _col(session, ddl, label)

    # Seed the single fno_config row if the table is empty
    from sqlalchemy import text as _text
    try:
        session.execute(_text("""
            INSERT INTO fno_config (
                telegram_fields, telegram_mode,
                nifty_sl_points,     nifty_target_points,     nifty_target_2_points,     nifty_target_3_points,
                banknifty_sl_points, banknifty_target_points, banknifty_target_2_points, banknifty_target_3_points,
                finnifty_sl_points,  finnifty_target_points,  finnifty_target_2_points,  finnifty_target_3_points,
                sensex_sl_points,    sensex_target_points,    sensex_target_2_points,    sensex_target_3_points,
                nifty_telegram, banknifty_telegram, finnifty_telegram, sensex_telegram
            )
            SELECT
                'header,direction,confidence,entry_mode,spot_atm,trades_list,active_trade,exit_reason,timestamp,dashboard_link',
                'full',
                30, 60, 90, 120,
                60, 120, 180, 240,
                30, 60, 90, 120,
                60, 120, 180, 240,
                TRUE, FALSE, FALSE, FALSE
            WHERE NOT EXISTS (SELECT 1 FROM fno_config)
        """))
        session.commit()
        logger.info("  Seeded default fno_config row.")
    except Exception as exc:
        session.rollback()
        logger.warning("  [skip] seed fno_config: %s", exc)

    # Widen SL/Target on existing rows still at the old tight defaults.
    # Condition: nifty_sl_points <= 20 means the old 10% SL values are in place.
    # Admin-customised rows (sl > 20) are left untouched.
    try:
        result = session.execute(_text("""
            UPDATE fno_config SET
                nifty_sl_points=30,     nifty_target_points=60,     nifty_target_2_points=90,     nifty_target_3_points=120,
                banknifty_sl_points=60, banknifty_target_points=120, banknifty_target_2_points=180, banknifty_target_3_points=240,
                finnifty_sl_points=30,  finnifty_target_points=60,  finnifty_target_2_points=90,   finnifty_target_3_points=120,
                sensex_sl_points=60,    sensex_target_points=120,   sensex_target_2_points=180,    sensex_target_3_points=240
            WHERE nifty_sl_points <= 20 AND nifty_target_points <= 30
        """))
        session.commit()
        if result.rowcount:
            logger.info("  Updated %s fno_config row(s) to wider SL/Target (1:2 R:R).", result.rowcount)
    except Exception as exc:
        session.rollback()
        logger.warning("  [skip] fno_config SL widening: %s", exc)

    # Fix FK constraints pointing to old 'broker_accounts' table — should be 'user_brokers'
    fk_fixes = [
        ("broker_holdings",  "broker_holdings_broker_account_id_fkey"),
        ("broker_positions", "broker_positions_broker_account_id_fkey"),
        ("broker_orders",    "broker_orders_broker_account_id_fkey"),
    ]
    from sqlalchemy import text
    for table, constraint in fk_fixes:
        try:
            exists = session.execute(text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name=:cn AND constraint_type='FOREIGN KEY'"
            ), {"cn": constraint}).fetchone()
            if exists:
                session.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"))
                session.execute(text(
                    f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                    f"FOREIGN KEY (broker_account_id) REFERENCES user_brokers(id) ON DELETE CASCADE"
                ))
                session.commit()
                logger.info(f"Fixed FK constraint {constraint} → user_brokers")
        except Exception as e:
            session.rollback()
            logger.warning(f"Could not fix FK constraint {constraint}: {e}")

    logger.info("Column checks complete.")

    # ── Data fix: activate all existing users ──────────────────
    # Signup is open (no invite code required) and every user should be
    # active. New rows default to active=TRUE, but any pre-existing user
    # left inactive on this database is flipped here. Idempotent: the
    # WHERE clause means repeat deploys touch zero rows.
    from sqlalchemy import text as _text
    try:
        result = session.execute(
            _text('UPDATE "user" SET active = TRUE WHERE active IS DISTINCT FROM TRUE')
        )
        session.commit()
        logger.info("Activated %s previously-inactive user(s).", result.rowcount)
    except Exception as exc:
        session.rollback()
        logger.warning("Could not activate users: %s", exc)


# ─────────────────────────────────────────────
# Index creation
# ─────────────────────────────────────────────

def ensure_indexes(session):
    """Create performance indexes that may be missing."""
    from sqlalchemy import text
    indexes = [
        ("CREATE INDEX IF NOT EXISTS idx_user_tenant_id ON \"user\"(tenant_id)",                             "idx_user_tenant_id"),
        ("CREATE INDEX IF NOT EXISTS idx_user_mobile ON \"user\"(mobile_number)",                            "idx_user_mobile"),
        ("CREATE INDEX IF NOT EXISTS idx_user_email ON \"user\"(email)",                                     "idx_user_email"),
        ("CREATE UNIQUE INDEX IF NOT EXISTS idx_research_list_symbol ON research_list(symbol)",               "idx_research_list_symbol"),
        ("CREATE INDEX IF NOT EXISTS idx_research_list_tenant ON research_list(tenant_id)",                   "idx_research_list_tenant"),
        ("CREATE INDEX IF NOT EXISTS idx_research_list_asset_type ON research_list(asset_type)",              "idx_research_list_asset_type"),
        ("CREATE INDEX IF NOT EXISTS idx_research_list_i_score ON research_list(i_score DESC NULLS LAST)",    "idx_research_list_i_score"),
        ("CREATE INDEX IF NOT EXISTS idx_research_run_user ON research_run(user_id)",                         "idx_research_run_user"),
        ("CREATE INDEX IF NOT EXISTS idx_research_run_date ON research_run(analysis_date)",                   "idx_research_run_date"),
        ("CREATE INDEX IF NOT EXISTS idx_research_run_cache ON research_run(cache_key)",                      "idx_research_run_cache"),
        ("CREATE INDEX IF NOT EXISTS idx_research_cache_key ON research_cache(cache_key)",                    "idx_research_cache_key"),
        ("CREATE INDEX IF NOT EXISTS idx_daily_signals_date ON daily_trading_signals(signal_date)",           "idx_daily_signals_date"),
        ("CREATE INDEX IF NOT EXISTS idx_daily_signals_tenant ON daily_trading_signals(tenant_id)",           "idx_daily_signals_tenant"),
        ("CREATE INDEX IF NOT EXISTS idx_broker_holdings_broker ON broker_holdings(broker_account_id)",       "idx_broker_holdings_broker"),
        ("CREATE INDEX IF NOT EXISTS idx_broker_holdings_symbol ON broker_holdings(symbol)",                  "idx_broker_holdings_symbol"),
        ("CREATE INDEX IF NOT EXISTS idx_portfolio_events_user ON portfolio_events(user_id)",                 "idx_portfolio_events_user"),
        ("CREATE INDEX IF NOT EXISTS idx_behavioural_alerts_user ON behavioural_alerts(user_id)",             "idx_behavioural_alerts_user"),
        ("CREATE INDEX IF NOT EXISTS idx_workflow_exec_user ON workflow_executions(user_id)",                 "idx_workflow_exec_user"),
        ("CREATE INDEX IF NOT EXISTS idx_workflow_exec_id ON workflow_executions(execution_id)",              "idx_workflow_exec_id"),
        ("CREATE INDEX IF NOT EXISTS idx_fno_signal_created ON fno_signal_history(created_at DESC)",        "idx_fno_signal_created"),
        ("CREATE INDEX IF NOT EXISTS idx_fno_signal_index_id ON fno_signal_history(index_id)",              "idx_fno_signal_index_id"),
        ("CREATE INDEX IF NOT EXISTS idx_fno_signal_trade_code ON fno_signal_history(trade_code)",          "idx_fno_signal_trade_code"),
    ]
    for ddl, label in indexes:
        try:
            session.execute(text(ddl))
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("  [skip] %s: %s", label, exc)
    logger.info("Index checks complete.")


# ─────────────────────────────────────────────
# DB extensions
# ─────────────────────────────────────────────

def ensure_extensions(session):
    from sqlalchemy import text
    for ext in ('uuid-ossp', 'pgcrypto'):
        try:
            session.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
            session.commit()
            logger.info("Extension %s ready.", ext)
        except Exception as exc:
            session.rollback()
            logger.warning("Extension %s skipped: %s", ext, exc)


# ─────────────────────────────────────────────
# Main table creation + column patching
# ─────────────────────────────────────────────

def create_tables_directly():
    """Fallback: create/patch schema directly using SQLAlchemy."""
    logger.info("Creating/patching database tables directly…")

    database_url = _fix_db_url(os.environ.get('DATABASE_URL', ''))
    os.environ['DATABASE_URL'] = database_url
    os.environ.setdefault('SESSION_SECRET', 'temp-migration-secret')
    os.environ.setdefault('ENVIRONMENT', 'production')

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    connect_args = {}
    if 'postgresql' in database_url:
        connect_args = {"sslmode": "require", "connect_timeout": 30}

    engine = create_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Extensions
    ensure_extensions(session)

    # 2. Create all tables (new tables only; existing tables are untouched)
    try:
        from app import db, app
        with app.app_context():
            db.create_all()
            logger.info("db.create_all() complete — all new tables created.")
    except Exception as exc:
        logger.error("db.create_all() failed: %s", exc)
        raise

    # 2b. Create raw-SQL tables not defined in SQLAlchemy models
    ensure_raw_tables(session)

    # 3. Add missing columns to existing tables
    ensure_missing_columns(session)

    # 4. Add missing indexes
    ensure_indexes(session)

    session.close()
    engine.dispose()
    logger.info("Direct table creation/patching complete.")


# ─────────────────────────────────────────────
# Default data seeding
# ─────────────────────────────────────────────

def seed_defaults():
    """Ensure default Tenant and AccountManager rows exist."""
    try:
        from app import db, app
        with app.app_context():
            from models import Tenant
            Tenant.get_or_create_default()
            logger.info("Default tenant ready.")

            from models import AccountManager
            AccountManager.get_or_create_default()
            logger.info("Default account manager ready.")
    except Exception as exc:
        logger.warning("seed_defaults() skipped: %s", exc)


def seed_initial_admin():
    """
    Idempotently create the first admin account.

    Credentials are read from environment variables so nothing sensitive is
    hard-coded in source:

        ADMIN_USERNAME   (default: admin)
        ADMIN_EMAIL      (required — skipped if not set)
        ADMIN_PASSWORD   (required — skipped if not set, must be ≥ 8 chars)
        ADMIN_SUPER      (default: true)

    The function is a pure no-op when:
      - ADMIN_EMAIL or ADMIN_PASSWORD are absent
      - an admin with that username or email already exists
    """
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()

    if not admin_email or not admin_password:
        logger.info(
            "seed_initial_admin: ADMIN_EMAIL / ADMIN_PASSWORD not set — skipping."
        )
        return

    if len(admin_password) < 8:
        logger.warning(
            "seed_initial_admin: ADMIN_PASSWORD is too short (< 8 chars) — skipping."
        )
        return

    admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
    is_super = os.environ.get("ADMIN_SUPER", "true").lower() not in ("0", "false", "no")

    try:
        from app import db, app
        with app.app_context():
            from models import Admin
            existing = Admin.query.filter(
                (Admin.username == admin_username) | (Admin.email == admin_email)
            ).first()
            if existing:
                logger.info(
                    "seed_initial_admin: admin '%s' already exists — skipping.",
                    existing.username,
                )
                return

            admin = Admin(
                username=admin_username,
                email=admin_email,
                is_super_admin=is_super,
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            db.session.commit()
            logger.info(
                "seed_initial_admin: created admin '%s' (%s) super=%s",
                admin_username,
                admin_email,
                is_super,
            )
    except Exception as exc:
        logger.warning("seed_initial_admin() failed: %s", exc)


def seed_research_list():
    """Merge all RESEARCH_LIST_STOCKS into research_list — safe to re-run (ON CONFLICT DO NOTHING)."""
    try:
        from seed_data import RESEARCH_LIST_STOCKS
        from app import db, app
        with app.app_context():
            from sqlalchemy import text
            inserted = 0
            skipped = 0
            with db.engine.begin() as conn:
                for stock in RESEARCH_LIST_STOCKS:
                    result = conn.execute(
                        text("""
                            INSERT INTO research_list (symbol, company_name, asset_type, sector, is_active, tenant_id)
                            VALUES (:symbol, :company_name, :asset_type, :sector, TRUE, 'live')
                            ON CONFLICT (symbol) DO NOTHING
                        """),
                        {
                            'symbol':       stock['symbol'],
                            'company_name': stock['company_name'],
                            'asset_type':   stock.get('asset_type', 'stocks'),
                            'sector':       stock.get('sector', ''),
                        }
                    )
                    if result.rowcount:
                        inserted += 1
                    else:
                        skipped += 1
            logger.info("Research list seed complete — %d inserted, %d already existed.", inserted, skipped)
    except Exception as exc:
        logger.warning("seed_research_list() skipped: %s", exc)


# ─────────────────────────────────────────────
# Alembic runner
# ─────────────────────────────────────────────

def run_migrations():
    """Try Alembic; fall back to direct creation/patching."""
    database_url = os.environ.get('DATABASE_URL', '')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
        os.environ['DATABASE_URL'] = database_url

    logger.info("Starting database migration…")

    try:
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully.")
    except Exception as exc:
        logger.warning("Alembic failed (%s) — switching to direct mode.", exc)
        create_tables_directly()

    # Always seed defaults and patch columns even after Alembic
    try:
        from app import db, app
        with app.app_context():
            from sqlalchemy.orm import sessionmaker
            Session = sessionmaker(bind=db.engine)
            session = Session()
            ensure_raw_tables(session)
            ensure_missing_columns(session)
            ensure_indexes(session)
            session.close()
    except Exception as exc:
        logger.warning("Post-Alembic column patch skipped: %s", exc)

    seed_defaults()
    seed_initial_admin()
    seed_research_list()

    # Seed I-Score pre-computed data (reuses already-loaded app module)
    try:
        import seed_iscore_data as _iscore_mod
        _iscore_mod.seed()
        logger.info("I-Score data seed complete.")
    except Exception as exc:
        logger.warning("I-Score seed skipped: %s", exc)

    # Seed blog posts (reuses already-loaded app module)
    try:
        import seed_blog_posts as _blog_mod
        _blog_mod.seed()
        logger.info("Blog posts seed complete.")
    except Exception as exc:
        logger.warning("Blog posts seed skipped: %s", exc)


# ─────────────────────────────────────────────
# Connectivity check
# ─────────────────────────────────────────────

def verify_database_connection():
    database_url = _fix_db_url(os.environ.get('DATABASE_URL', ''))

    if not database_url:
        logger.error("DATABASE_URL not set.")
        return False

    try:
        from sqlalchemy import create_engine, text

        connect_args = {}
        if 'postgresql' in database_url:
            # Use require in production (Railway), prefer in dev (Replit local PG
            # doesn't expose SSL).  Both give encrypted transit on Railway; on
            # Replit the connection is already loopback-only so prefer is safe.
            _env = os.environ.get("ENVIRONMENT", "development")
            _ssl = "require" if _env == "production" else "prefer"
            connect_args = {"sslmode": _ssl, "connect_timeout": 30}

        engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        logger.info("Database connection verified.")
        return True
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        return False


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Railway Migration Script — Target Capital")
    logger.info("=" * 60)

    if not verify_database_connection():
        logger.error("Cannot proceed without a database connection.")
        sys.exit(1)

    run_migrations()

    logger.info("=" * 60)
    logger.info("Migration script completed successfully.")
    logger.info("=" * 60)
