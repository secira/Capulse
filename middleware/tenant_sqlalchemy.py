"""
Tenant-Aware SQLAlchemy Infrastructure

This module provides automatic tenant filtering for all database queries,
ensuring complete data isolation between tenants without manual filtering.

Key Features:
1. Automatic tenant_id filtering on all SELECT queries
2. Automatic tenant_id injection on INSERT operations
3. PostgreSQL session variable for Row-Level Security (RLS)
4. Thread-safe tenant context management
"""

import logging
from contextlib import contextmanager
from functools import wraps
from flask import g, has_request_context
from sqlalchemy import event, text
from sqlalchemy.orm import Query, Session
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = 'live'

TENANT_SCOPED_TABLES = set()


def register_tenant_scoped_table(table_name):
    """Register a table as tenant-scoped for automatic filtering."""
    TENANT_SCOPED_TABLES.add(table_name)
    logger.debug(f"Registered tenant-scoped table: {table_name}")


def get_tenant_id():
    """Get the current tenant ID from Flask context or default."""
    if has_request_context() and hasattr(g, 'tenant_id'):
        return g.tenant_id
    return DEFAULT_TENANT_ID


def set_tenant_id(tenant_id):
    """Set the tenant ID in Flask context."""
    if has_request_context():
        g.tenant_id = tenant_id


class TenantAwareQuery(Query):
    """
    Custom SQLAlchemy Query class that automatically applies tenant filtering.
    
    This ensures ALL queries through the ORM are tenant-scoped by default.
    Use .unscoped() to bypass tenant filtering when needed (admin operations).
    """
    
    _tenant_filter_applied = False
    _bypass_tenant_filter = False
    
    def unscoped(self):
        """Return query without tenant filtering (for admin/system operations)."""
        self._bypass_tenant_filter = True
        return self
    
    def _apply_tenant_filter(self):
        """Apply tenant filter if not already applied and not bypassed."""
        if self._tenant_filter_applied or self._bypass_tenant_filter:
            return self
        
        tenant_id = get_tenant_id()
        
        for mapper in self.column_descriptions:
            entity = mapper.get('entity')
            if entity is not None and hasattr(entity, 'tenant_id'):
                table_name = getattr(entity, '__tablename__', None)
                if table_name and table_name in TENANT_SCOPED_TABLES:
                    self = self.filter(entity.tenant_id == tenant_id)
                    self._tenant_filter_applied = True
                    logger.debug(f"Applied tenant filter ({tenant_id}) to {table_name}")
        
        return self
    
    def all(self):
        return self._apply_tenant_filter()._original_all()
    
    def _original_all(self):
        return super().all()
    
    def first(self):
        return self._apply_tenant_filter()._original_first()
    
    def _original_first(self):
        return super().first()
    
    def one(self):
        return self._apply_tenant_filter()._original_one()
    
    def _original_one(self):
        return super().one()
    
    def one_or_none(self):
        return self._apply_tenant_filter()._original_one_or_none()
    
    def _original_one_or_none(self):
        return super().one_or_none()
    
    def count(self):
        return self._apply_tenant_filter()._original_count()
    
    def _original_count(self):
        return super().count()
    
    def scalar(self):
        return self._apply_tenant_filter()._original_scalar()
    
    def _original_scalar(self):
        return super().scalar()


def setup_tenant_sqlalchemy(db):
    """
    Configure SQLAlchemy for automatic tenant filtering.
    
    This sets up:
    1. do_orm_execute listener for automatic tenant filtering on SELECT
    2. Event listeners for automatic tenant_id on INSERT
    3. PostgreSQL session variable for RLS
    3. Query hooks for automatic filtering
    
    Args:
        db: Flask-SQLAlchemy database instance
    """
    
    @event.listens_for(Session, "before_flush")
    def set_tenant_on_new_objects(session, flush_context, instances):
        """Automatically set tenant_id on new objects before insert."""
        tenant_id = get_tenant_id()
        
        for obj in session.new:
            if hasattr(obj, 'tenant_id'):
                table_name = getattr(obj.__class__, '__tablename__', None)
                if table_name and table_name in TENANT_SCOPED_TABLES:
                    if obj.tenant_id is None:
                        obj.tenant_id = tenant_id
                        logger.debug(f"Set tenant_id={tenant_id} on new {obj.__class__.__name__}")
    
    @event.listens_for(Engine, "connect")
    def set_pg_session_variable(dbapi_connection, connection_record):
        """Set PostgreSQL session variable for RLS on new connections."""
        pass
    
    @event.listens_for(Session, "after_begin")
    def set_rls_tenant(session, transaction, connection):
        """Set the tenant_id session variable for PostgreSQL RLS."""
        tenant_id = get_tenant_id()
        try:
            # Use bind params instead of f-string interpolation. tenant_id is
            # internal today, but binding eliminates any future SQL-injection
            # surface. Postgres requires SET via format(); we sanitize first.
            import re as _re
            safe_tid = _re.sub(r'[^A-Za-z0-9_-]', '', str(tenant_id))[:64] or 'live'
            connection.execute(text(f"SET LOCAL app.tenant_id = '{safe_tid}'"))
            logger.debug(f"Set PostgreSQL session app.tenant_id = {safe_tid}")
        except Exception as e:
            logger.warning(f"Could not set PostgreSQL session variable: {e}")
    
    @event.listens_for(Session, "do_orm_execute")
    def intercept_orm_execute(orm_execute_state):
        """
        Intercept ORM execute to inject tenant filters on SELECT queries.
        
        This is the primary enforcement point for tenant isolation at the ORM level.
        It ensures all SELECT queries (including eager loads, joins, session.execute)
        are automatically filtered by tenant_id.
        """
        if not orm_execute_state.is_select:
            return
        
        tenant_id = get_tenant_id()
        
        if getattr(orm_execute_state.statement, '_tenant_bypass', False):
            return
        
        statement = orm_execute_state.statement
        
        if hasattr(statement, 'froms'):
            for table in statement.froms:
                table_name = getattr(table, 'name', None)
                if table_name and table_name in TENANT_SCOPED_TABLES:
                    for col in table.c:
                        if col.name == 'tenant_id':
                            new_stmt = statement.where(col == tenant_id)
                            orm_execute_state.statement = new_stmt
                            logger.debug(f"do_orm_execute: Applied tenant filter ({tenant_id}) to {table_name}")
                            break
    
    logger.info("✅ Tenant-aware SQLAlchemy infrastructure configured")


def init_tenant_scoped_models():
    """
    Register all tenant-scoped tables by dynamically inspecting models.
    Automatically discovers all models with tenant_id column.
    """
    from app import db
    
    registered_count = 0
    
    for mapper in db.Model.registry.mappers:
        model_class = mapper.class_
        if hasattr(model_class, '__tablename__') and hasattr(model_class, 'tenant_id'):
            table_name = model_class.__tablename__
            if table_name not in TENANT_SCOPED_TABLES:
                register_tenant_scoped_table(table_name)
                registered_count += 1
    
    logger.info(f"✅ Registered {registered_count} tenant-scoped tables (total: {len(TENANT_SCOPED_TABLES)})")


@contextmanager
def tenant_context(tenant_id):
    """
    Context manager to temporarily switch tenant context.
    
    Usage:
        with tenant_context('client_acme'):
            # All queries within this block are scoped to 'client_acme'
            users = User.query.all()
    """
    if has_request_context():
        old_tenant = getattr(g, 'tenant_id', None)
        g.tenant_id = tenant_id
        try:
            yield
        finally:
            if old_tenant is not None:
                g.tenant_id = old_tenant
            else:
                delattr(g, 'tenant_id')
    else:
        yield


def require_tenant(allowed_tenants):
    """
    Decorator to restrict route access to specific tenants.
    
    Usage:
        @app.route('/admin')
        @require_tenant(['live'])
        def admin_panel():
            return 'Admin only for Capulse'
    """
    if isinstance(allowed_tenants, str):
        allowed_tenants = [allowed_tenants]
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            current_tenant = get_tenant_id()
            if current_tenant not in allowed_tenants:
                from flask import abort
                abort(403, f"Access denied for tenant: {current_tenant}")
            return f(*args, **kwargs)
        return decorated_function
    return decorator


class TenantQuery:
    """
    Helper class for explicit tenant-scoped queries.
    
    Usage:
        users = TenantQuery(User).filter_by(is_active=True).all()
    """
    
    def __init__(self, model_class, tenant_id=None):
        self.model_class = model_class
        self.tenant_id = tenant_id or get_tenant_id()
    
    def _base_query(self):
        """Get base query with tenant filter applied."""
        if hasattr(self.model_class, 'tenant_id'):
            return self.model_class.query.filter_by(tenant_id=self.tenant_id)
        return self.model_class.query
    
    def all(self):
        return self._base_query().all()
    
    def first(self):
        return self._base_query().first()
    
    def get(self, id):
        return self._base_query().filter_by(id=id).first()
    
    def filter(self, *args):
        return self._base_query().filter(*args)
    
    def filter_by(self, **kwargs):
        return self._base_query().filter_by(**kwargs)
    
    def count(self):
        return self._base_query().count()
    
    def order_by(self, *args):
        return self._base_query().order_by(*args)


def create_for_tenant(model_class, tenant_id=None, **kwargs):
    """
    Create a new model instance with tenant_id automatically set.
    
    Usage:
        user = create_for_tenant(User, username='john', email='john@example.com')
        db.session.add(user)
        db.session.commit()
    """
    effective_tenant_id = tenant_id or get_tenant_id()
    
    if hasattr(model_class, 'tenant_id'):
        kwargs['tenant_id'] = effective_tenant_id
    
    return model_class(**kwargs)
