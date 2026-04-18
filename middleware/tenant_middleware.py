"""
Multi-Tenant Middleware for Target Capital

This module provides tenant resolution and context management for the 
multi-tenant architecture. It resolves the current tenant from various
sources (subdomain, header, user) and stores it in Flask's request context.

Base tenant: 'live' - Target Capital's primary deployment
"""

from functools import wraps
from flask import g, request, current_app
import logging

logger = logging.getLogger(__name__)

# Default tenant ID for Target Capital
DEFAULT_TENANT_ID = 'live'


def get_tenant_from_subdomain(host):
    """
    Extract tenant ID from subdomain.
    
    Examples:
        - client1.targetcapital.ai -> 'client1'
        - app.targetcapital.ai -> 'live' (default)
        - targetcapital.ai -> 'live' (default)
        - localhost:5000 -> 'live' (default)
        - *.worf.replit.dev -> 'live' (Replit dev domain)
        - *.replit.dev -> 'live' (Replit domain)
    """
    if not host:
        return None
    
    # Remove port if present
    host = host.split(':')[0]
    
    # Skip localhost and IP addresses
    if host in ('localhost', '127.0.0.1') or host.startswith('192.168.'):
        return None
    
    # Skip development/hosting platform domains - always use 'live' tenant
    if '.replit.dev' in host or '.replit.app' in host or '.repl.co' in host:
        return None
    if '.railway.app' in host or '.up.railway.app' in host:
        return None
    
    # Known production domains that don't indicate a tenant
    main_domains = ['targetcapital.ai', 'in.targetcapital.ai', 'app.targetcapital.ai']
    
    # Check if this is a subdomain of the main domain
    parts = host.split('.')
    
    # If we have a subdomain (e.g., client1.targetcapital.ai)
    if len(parts) >= 3:
        subdomain = parts[0]
        
        # Skip common subdomains that don't indicate tenant
        if subdomain in ('www', 'app', 'api', 'in', 'usa', 'admin'):
            return None
        
        # This looks like a tenant subdomain
        return subdomain
    
    return None


def get_tenant_from_header():
    """
    Get tenant ID from X-Tenant-ID request header.
    Useful for API clients and testing.
    """
    return request.headers.get('X-Tenant-ID')


def get_tenant_from_user():
    """
    Get tenant ID from the currently logged in user.
    """
    from flask_login import current_user
    
    try:
        if current_user and current_user.is_authenticated:
            return getattr(current_user, 'tenant_id', None)
    except Exception:
        pass
    
    return None


def resolve_tenant_id():
    """
    Resolve the current tenant ID from various sources.
    
    Priority order:
    1. X-Tenant-ID header (for API clients)
    2. Current authenticated user's tenant_id
    3. Subdomain (for white-label deployments)
    4. Default to 'live'
    """
    # Priority 1: Header override (useful for testing and API clients)
    tenant_id = get_tenant_from_header()
    if tenant_id:
        logger.debug(f"Tenant resolved from header: {tenant_id}")
        return tenant_id
    
    # Priority 2: Current user's tenant
    tenant_id = get_tenant_from_user()
    if tenant_id:
        logger.debug(f"Tenant resolved from user: {tenant_id}")
        return tenant_id
    
    # Priority 3: Subdomain
    tenant_id = get_tenant_from_subdomain(request.host)
    if tenant_id:
        logger.debug(f"Tenant resolved from subdomain: {tenant_id}")
        return tenant_id
    
    # Default: 'live' tenant (Target Capital)
    return DEFAULT_TENANT_ID


def get_current_tenant_id():
    """
    Get the current tenant ID from Flask's request context.
    Falls back to 'live' if not in request context.
    """
    from flask import has_request_context
    
    if has_request_context() and hasattr(g, 'tenant_id'):
        return g.tenant_id
    
    return DEFAULT_TENANT_ID


def get_current_tenant():
    """
    Get the current Tenant model instance.
    """
    from models import Tenant
    
    tenant_id = get_current_tenant_id()
    return Tenant.query.get(tenant_id)


def init_tenant_middleware(app):
    """
    Initialize tenant middleware for the Flask application.
    This sets up the before_request handler to resolve tenant.
    """
    @app.before_request
    def set_tenant_context():
        """Set tenant context for each request."""
        try:
            g.tenant_id = resolve_tenant_id()
        except Exception as exc:
            logger.warning(f"Tenant resolution failed ({type(exc).__name__}), rolling back and defaulting to '{DEFAULT_TENANT_ID}'")
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
            g.tenant_id = DEFAULT_TENANT_ID
        logger.debug(f"Request tenant context: {g.tenant_id}")
    
    @app.after_request
    def add_tenant_header(response):
        """Add tenant ID to response headers for debugging."""
        if hasattr(g, 'tenant_id'):
            response.headers['X-Tenant-ID'] = g.tenant_id
        return response


def require_tenant(tenant_id):
    """
    Decorator to require a specific tenant for a route.
    
    Usage:
        @app.route('/admin')
        @require_tenant('live')
        def admin_panel():
            return 'Admin panel'
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            current = get_current_tenant_id()
            if current != tenant_id:
                from flask import abort
                abort(403, f"Access denied for tenant: {current}")
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def tenant_scope(model_class):
    """
    Decorator/helper to automatically filter queries by tenant.
    
    Usage:
        # Get all users for current tenant
        users = tenant_scope(User).all()
    """
    tenant_id = get_current_tenant_id()
    return model_class.query.filter_by(tenant_id=tenant_id)


class TenantQuery:
    """
    Helper class for tenant-scoped database queries.
    
    This class provides explicit tenant filtering for database queries.
    For automatic filtering, use the TenantAwareQuery infrastructure.
    
    Usage:
        from middleware.tenant_middleware import TenantQuery
        
        # Get all users for current tenant
        users = TenantQuery(User).all()
        
        # Filter further
        active_users = TenantQuery(User).filter_by(active=True).all()
    """
    
    def __init__(self, model_class, tenant_id=None):
        self.model_class = model_class
        self.tenant_id = tenant_id or get_current_tenant_id()
    
    def _base_query(self):
        """Get base query with tenant filter applied."""
        if hasattr(self.model_class, 'tenant_id'):
            return self.model_class.query.filter_by(tenant_id=self.tenant_id)
        return self.model_class.query
    
    def all(self):
        """Get all records for current tenant."""
        return self._base_query().all()
    
    def first(self):
        """Get first record for current tenant."""
        return self._base_query().first()
    
    def get(self, id):
        """Get record by ID, ensuring it belongs to current tenant."""
        return self._base_query().filter_by(id=id).first()
    
    def filter(self, *args):
        """Add additional filters."""
        return self._base_query().filter(*args)
    
    def filter_by(self, **kwargs):
        """Add additional filter_by conditions."""
        return self._base_query().filter_by(**kwargs)
    
    def count(self):
        """Count records for current tenant."""
        return self._base_query().count()
    
    def order_by(self, *args):
        """Add ordering to query."""
        return self._base_query().order_by(*args)


def create_for_tenant(model_class, tenant_id=None, **kwargs):
    """
    Create a new model instance with the current tenant_id automatically set.
    
    Usage:
        user = create_for_tenant(User, username='john', email='john@example.com')
        db.session.add(user)
        db.session.commit()
    """
    effective_tenant_id = tenant_id or get_current_tenant_id()
    
    if hasattr(model_class, 'tenant_id'):
        kwargs['tenant_id'] = effective_tenant_id
    
    return model_class(**kwargs)


def verify_resource_ownership(model_class, resource_id, user_id=None, user_id_field='user_id'):
    """
    SECURITY: Verify that a resource belongs to the current user.
    Returns the resource if owned, None otherwise.
    
    This prevents unauthorized access via URL manipulation.
    
    Usage:
        broker = verify_resource_ownership(BrokerAccount, broker_id)
        if not broker:
            return jsonify({'error': 'Resource not found'}), 404
    
    Args:
        model_class: The SQLAlchemy model class
        resource_id: The ID of the resource to verify
        user_id: Optional user ID (defaults to current_user.id)
        user_id_field: The field name for user ownership (default: 'user_id')
    
    Returns:
        The resource if owned by the user, None otherwise
    """
    from flask_login import current_user
    
    if user_id is None:
        if not current_user.is_authenticated:
            return None
        user_id = current_user.id
    
    # Build filter criteria
    filter_kwargs = {'id': resource_id, user_id_field: user_id}
    
    # Add tenant filter if model has tenant_id
    if hasattr(model_class, 'tenant_id'):
        filter_kwargs['tenant_id'] = get_current_tenant_id()
    
    return model_class.query.filter_by(**filter_kwargs).first()


def require_resource_ownership(model_class, id_param='id', user_id_field='user_id'):
    """
    Decorator to require resource ownership for a route.
    
    Usage:
        @app.route('/api/broker/<int:broker_id>')
        @login_required
        @require_resource_ownership(BrokerAccount, id_param='broker_id')
        def get_broker(broker_id):
            # g.resource contains the verified resource
            return jsonify(g.resource.to_dict())
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import abort
            
            resource_id = kwargs.get(id_param)
            if resource_id is None:
                abort(400, "Resource ID required")
            
            resource = verify_resource_ownership(model_class, resource_id, user_id_field=user_id_field)
            if resource is None:
                abort(404, "Resource not found")
            
            g.resource = resource
            return f(*args, **kwargs)
        return decorated_function
    return decorator
