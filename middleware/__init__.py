"""
Middleware package for Capulse

Contains middleware for:
- Multi-tenant support and tenant resolution
"""

from middleware.tenant_middleware import (
    init_tenant_middleware,
    get_current_tenant_id,
    get_current_tenant,
    tenant_scope,
    TenantQuery,
    create_for_tenant,
    require_tenant,
    DEFAULT_TENANT_ID,
)

__all__ = [
    'init_tenant_middleware',
    'get_current_tenant_id',
    'get_current_tenant',
    'tenant_scope',
    'TenantQuery',
    'create_for_tenant',
    'require_tenant',
    'DEFAULT_TENANT_ID',
]
