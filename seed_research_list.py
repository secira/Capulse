#!/usr/bin/env python3
"""
Research List Seeder
Runs on every Railway deployment to ensure all NSE EQ stocks (~2167) are present.
Safe to run multiple times — uses INSERT ... ON CONFLICT DO NOTHING logic.
Uses bulk operations for speed (~2167 stocks in seconds, not minutes).
"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def seed():
    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url:
        logger.error("DATABASE_URL not set — skipping seed")
        return

    # Normalize Railway's postgres:// prefix
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    if database_url.startswith('postgresql://') and '+psycopg2' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    os.environ['DATABASE_URL'] = database_url
    os.environ.setdefault('SESSION_SECRET', 'temp-seed-secret')
    os.environ.setdefault('ENVIRONMENT', 'production')

    try:
        from seed_data import RESEARCH_LIST_STOCKS
    except ImportError:
        logger.error("seed_data.py not found — cannot seed")
        return

    logger.info(f"Seed file contains {len(RESEARCH_LIST_STOCKS)} stocks")

    try:
        from app import app, db
        from sqlalchemy import text

        with app.app_context():
            # ── Fast path: count active rows ───────────────────────────────
            current_count = db.session.execute(
                text("SELECT COUNT(*) FROM research_list WHERE is_active = TRUE")
            ).scalar()
            logger.info(f"Current research_list active count: {current_count}")

            if current_count >= len(RESEARCH_LIST_STOCKS):
                logger.info("Research list already fully seeded — nothing to do")
                return

            # ── Fetch all existing symbols in one query ────────────────────
            existing_symbols = set(
                row[0] for row in db.session.execute(
                    text("SELECT symbol FROM research_list")
                ).fetchall()
            )
            logger.info(f"Existing symbols in DB: {len(existing_symbols)}")

            # ── Split into inserts and updates ─────────────────────────────
            to_insert = []
            to_update = []
            for stock in RESEARCH_LIST_STOCKS:
                if stock['symbol'] in existing_symbols:
                    to_update.append(stock)
                else:
                    to_insert.append(stock)

            logger.info(f"To insert: {len(to_insert)}, To update (metadata only): {len(to_update)}")

            # ── Bulk INSERT new stocks in batches of 500 ───────────────────
            BATCH = 500
            inserted = 0
            for i in range(0, len(to_insert), BATCH):
                batch = to_insert[i:i + BATCH]
                if not batch:
                    continue
                values_sql = ', '.join(
                    f"(:sym_{j}, :name_{j}, :atype_{j}, :sector_{j}, TRUE, 'live')"
                    for j in range(len(batch))
                )
                params = {}
                for j, s in enumerate(batch):
                    params[f'sym_{j}'] = s['symbol']
                    params[f'name_{j}'] = s['company_name']
                    params[f'atype_{j}'] = s['asset_type']
                    params[f'sector_{j}'] = s['sector']
                db.session.execute(
                    text(
                        f"INSERT INTO research_list (symbol, company_name, asset_type, sector, is_active, tenant_id) "
                        f"VALUES {values_sql} ON CONFLICT (symbol) DO NOTHING"
                    ),
                    params
                )
                db.session.commit()
                inserted += len(batch)
                logger.info(f"  Inserted batch: {inserted}/{len(to_insert)}")

            # ── Bulk UPDATE existing stocks (metadata only, never i_score) ─
            updated = 0
            for i in range(0, len(to_update), BATCH):
                batch = to_update[i:i + BATCH]
                for s in batch:
                    db.session.execute(
                        text(
                            "UPDATE research_list SET company_name=:name, asset_type=:atype, "
                            "sector=:sector, is_active=TRUE, tenant_id=COALESCE(NULLIF(tenant_id,''), 'live') "
                            "WHERE symbol=:sym"
                        ),
                        {'name': s['company_name'], 'atype': s['asset_type'],
                         'sector': s['sector'], 'sym': s['symbol']}
                    )
                db.session.commit()
                updated += len(batch)
                logger.info(f"  Updated batch: {updated}/{len(to_update)}")

            final_count = db.session.execute(
                text("SELECT COUNT(*) FROM research_list WHERE is_active = TRUE")
            ).scalar()
            logger.info(f"Seed complete — {len(to_insert)} inserted, {len(to_update)} updated. Total active: {final_count}")

    except Exception as e:
        logger.error(f"Seed failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("Research List Seeder Starting")
    logger.info("=" * 50)
    seed()
    logger.info("=" * 50)
    logger.info("Seeder finished")
    logger.info("=" * 50)
