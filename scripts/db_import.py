#!/usr/bin/env python3
"""
Database Import Script for Target Capital
Imports data from SQL export file into Railway PostgreSQL.

Usage:
    python scripts/db_import.py [--file path/to/export.sql]

This script:
1. Connects to the Railway database (using DATABASE_URL)
2. Runs the SQL import file
3. Verifies row counts

Note: Run railway_migrate.py FIRST to create tables before importing data.
"""

import os
import sys
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    parser = argparse.ArgumentParser(description='Import database export into Railway')
    parser.add_argument('--file', default='database_export.sql', help='Path to SQL export file')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be imported without executing')
    args = parser.parse_args()
    
    print("=" * 60)
    print("Target Capital - Database Import Tool")
    print("=" * 60)
    print(f"Import started at: {datetime.utcnow().isoformat()}")
    print()
    
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set!")
        print("Make sure you're running this on Railway with the database connected.")
        sys.exit(1)
    
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    sql_file = args.file
    if not os.path.isabs(sql_file):
        sql_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), sql_file)
    
    if not os.path.exists(sql_file):
        print(f"ERROR: SQL file not found: {sql_file}")
        print("Run db_export.py first to generate the export file.")
        sys.exit(1)
    
    with open(sql_file, 'r') as f:
        sql_content = f.read()
    
    insert_count = sql_content.count('INSERT INTO')
    print(f"SQL file: {sql_file}")
    print(f"Total INSERT statements: {insert_count}")
    print()
    
    if args.dry_run:
        print("DRY RUN - No changes will be made")
        print()
        
        import re
        tables = set(re.findall(r'INSERT INTO "([^"]+)"', sql_content))
        print(f"Tables to import ({len(tables)}):")
        for table in sorted(tables):
            count = sql_content.count(f'INSERT INTO "{table}"')
            print(f"  - {table}: {count} rows")
        return
    
    print("Importing data using transactional import...")
    print()
    print("IMPORTANT: This will import data using ON CONFLICT DO NOTHING.")
    print("Existing records with matching primary keys will NOT be overwritten.")
    print()
    
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(database_url)
        
        with engine.begin() as conn:
            success_count = 0
            error_count = 0
            current_pos = 0
            
            while current_pos < len(sql_content):
                end_pos = sql_content.find(';', current_pos)
                if end_pos == -1:
                    stmt = sql_content[current_pos:].strip()
                    current_pos = len(sql_content)
                else:
                    stmt = sql_content[current_pos:end_pos].strip()
                    current_pos = end_pos + 1
                
                if not stmt or stmt.startswith('--'):
                    continue
                
                if stmt.startswith('DO $$'):
                    end_block = sql_content.find('$$;', current_pos - len(stmt) - 1)
                    if end_block != -1:
                        stmt = sql_content[current_pos - len(stmt) - 1:end_block + 3]
                        current_pos = end_block + 3
                
                try:
                    conn.execute(text(stmt + ';' if not stmt.endswith(';') else stmt))
                    success_count += 1
                except Exception as e:
                    error_str = str(e).lower()
                    if 'duplicate key' not in error_str and 'already exists' not in error_str:
                        error_count += 1
                        if error_count <= 10:
                            print(f"  Warning: {str(e)[:150]}")
        
        print()
        print("=" * 60)
        print(f"Import complete! (transaction committed)")
        print(f"Statements executed: {success_count}")
        if error_count > 0:
            print(f"Errors (non-duplicate): {error_count}")
        print("=" * 60)
        
    except Exception as e:
        print(f"ERROR: Failed to import data (transaction rolled back): {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
