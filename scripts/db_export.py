#!/usr/bin/env python3
"""
Database Export Script for Target Capital
Exports critical data from Replit PostgreSQL to SQL file for Railway migration.

Usage:
    python scripts/db_export.py

Output:
    Creates database_export.sql in project root
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from sqlalchemy import text

CRITICAL_TABLES = [
    'tenants',
    'user',
    'admins',
    'user_brokers',
    'broker_holdings',
    'broker_positions',
    'broker_orders',
    'portfolio',
    'subscription',
    'payment',
    'blog_posts',
    'ai_stock_picks',
    'watchlist_item',
    'user_risk_profile',
    'chatbot_knowledge_base',
    'manual_equity_holdings',
    'manual_mutual_fund_holdings',
    'manual_fixed_deposit_holdings',
    'manual_real_estate_holdings',
    'manual_commodity_holdings',
    'manual_cryptocurrency_holdings',
    'manual_insurance_holdings',
    'manual_bank_accounts',
    'manual_futures_options_holdings',
    'trading_signal',
    'trading_assets',
    'daily_trading_signals',
    'portfolio_preferences',
    'referral',
]

EXCLUDE_FROM_EXPORT = [
    'alembic_version',
    'research_cache',
    'research_run',
    'research_signal_component',
    'research_evidence',
    'vector_documents',
    'portfolio_document_chunks',
    'portfolio_knowledge_base',
    'research_conversations',
    'research_messages',
    'chat_conversations',
    'chat_messages',
    'broker_sync_logs',
]


def escape_value(val):
    """Escape value for SQL INSERT statement with proper PostgreSQL escaping"""
    if val is None:
        return 'NULL'
    elif isinstance(val, bool):
        return 'TRUE' if val else 'FALSE'
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, datetime):
        return f"'{val.isoformat()}'"
    elif isinstance(val, dict) or isinstance(val, list):
        import json
        json_str = json.dumps(val, ensure_ascii=False)
        json_str = json_str.replace("\\", "\\\\").replace("'", "''")
        json_str = json_str.replace("\n", "\\n").replace("\r", "\\r").replace("\x00", "")
        return f"E'{json_str}'"
    else:
        escaped = str(val)
        escaped = escaped.replace("\\", "\\\\").replace("'", "''")
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\x00", "")
        if "\\n" in escaped or "\\r" in escaped:
            return f"E'{escaped}'"
        return f"'{escaped}'"


def get_table_columns(table_name):
    """Get column names for a table"""
    result = db.session.execute(text(f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = '{table_name}' 
        ORDER BY ordinal_position
    """))
    return [row[0] for row in result]


def export_table(table_name, output_file):
    """Export a single table to SQL INSERT statements"""
    try:
        columns = get_table_columns(table_name)
        if not columns:
            print(f"  Skipping {table_name}: No columns found")
            return 0
        
        result = db.session.execute(text(f'SELECT * FROM "{table_name}"'))
        rows = result.fetchall()
        
        if not rows:
            print(f"  {table_name}: 0 rows (empty)")
            return 0
        
        output_file.write(f"\n-- Table: {table_name}\n")
        output_file.write(f"-- Exported: {datetime.utcnow().isoformat()}\n")
        output_file.write(f"-- Rows: {len(rows)}\n\n")
        
        for row in rows:
            values = [escape_value(val) for val in row]
            col_names = ', '.join([f'"{c}"' for c in columns])
            val_str = ', '.join(values)
            output_file.write(f'INSERT INTO "{table_name}" ({col_names}) VALUES ({val_str}) ON CONFLICT DO NOTHING;\n')
        
        print(f"  {table_name}: {len(rows)} rows exported")
        return len(rows)
    
    except Exception as e:
        print(f"  Error exporting {table_name}: {e}")
        return 0


def main():
    print("=" * 60)
    print("Target Capital - Database Export Tool")
    print("=" * 60)
    print(f"Export started at: {datetime.utcnow().isoformat()}")
    print()
    
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database_export.sql')
    
    with app.app_context():
        existing_tables_result = db.session.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """))
        existing_tables = [row[0] for row in existing_tables_result]
        
        tables_to_export = [t for t in CRITICAL_TABLES if t in existing_tables]
        
        print(f"Found {len(tables_to_export)} critical tables to export:")
        for t in tables_to_export:
            print(f"  - {t}")
        print()
        
        total_rows = 0
        
        with open(output_path, 'w') as f:
            f.write("-- Target Capital Database Export\n")
            f.write(f"-- Generated: {datetime.utcnow().isoformat()}\n")
            f.write("-- Source: Replit Development Database\n")
            f.write("-- Target: Railway Production Database\n")
            f.write("--\n")
            f.write("-- INSTRUCTIONS:\n")
            f.write("-- 1. Run railway_migrate.py first to create tables\n")
            f.write("-- 2. Then import this file: psql $DATABASE_URL < database_export.sql\n")
            f.write("--\n\n")
            
            f.write("-- Disable foreign key checks during import\n")
            f.write("SET session_replication_role = 'replica';\n\n")
            
            for table_name in tables_to_export:
                rows = export_table(table_name, f)
                total_rows += rows
            
            f.write("\n-- Re-enable foreign key checks\n")
            f.write("SET session_replication_role = 'origin';\n\n")
            
            f.write("-- Reset sequences to max values (only for tables with integer id)\n")
            f.write("DO $$ \n")
            f.write("DECLARE seq_name TEXT; max_val BIGINT;\n")
            f.write("BEGIN\n")
            for table_name in tables_to_export:
                if table_name not in ['tenants']:
                    f.write(f"  SELECT pg_get_serial_sequence('\"{table_name}\"', 'id') INTO seq_name;\n")
                    f.write(f"  IF seq_name IS NOT NULL THEN\n")
                    f.write(f"    SELECT COALESCE(MAX(id), 1) INTO max_val FROM \"{table_name}\";\n")
                    f.write(f"    EXECUTE format('SELECT setval(%%L, %%s)', seq_name, max_val);\n")
                    f.write(f"  END IF;\n")
            f.write("END $$;\n")
            
            f.write("\n-- Export complete\n")
        
        print()
        print("=" * 60)
        print(f"Export complete!")
        print(f"Total rows exported: {total_rows}")
        print(f"Output file: {output_path}")
        print("=" * 60)


if __name__ == '__main__':
    main()
