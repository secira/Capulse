"""
Per-Tenant Encryption Service

This module provides tenant-specific encryption for sensitive data columns
including PAN numbers, bank account details, and KYC documents.

Key Architecture:
1. Master encryption key stored in environment (ENCRYPTION_MASTER_KEY)
2. Per-tenant data encryption keys derived using HKDF
3. Field-level Fernet encryption for text columns
4. AES-GCM for binary data (documents)

Security Features:
- Unique encryption key per tenant (derived from master key + tenant_id)
- Keys are never stored, always derived on-demand
- Supports key rotation via master key version
- Audit logging for encryption operations
"""

import os
import base64
import hashlib
import logging
from functools import lru_cache
from typing import Optional, Union
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

ENCRYPTION_MASTER_KEY_ENV = 'ENCRYPTION_MASTER_KEY'
DEFAULT_KEY_VERSION = 1


class TenantEncryptionService:
    """
    Provides per-tenant encryption/decryption using derived keys.
    
    Usage:
        service = TenantEncryptionService()
        encrypted = service.encrypt('live', 'ABCDE1234F')  # PAN number
        decrypted = service.decrypt('live', encrypted)
    """
    
    def __init__(self, master_key: Optional[bytes] = None):
        """
        Initialize encryption service.
        
        Args:
            master_key: Optional master key bytes. If not provided, 
                       reads from ENCRYPTION_MASTER_KEY environment variable.
        """
        self._master_key = master_key or self._load_master_key()
        self._key_version = DEFAULT_KEY_VERSION
    
    def _load_master_key(self) -> bytes:
        """Load master encryption key from environment."""
        key_str = os.environ.get(ENCRYPTION_MASTER_KEY_ENV)
        
        if not key_str:
            environment = os.environ.get('ENVIRONMENT', 'development')
            if environment == 'production':
                raise ValueError(
                    f"ENCRYPTION_MASTER_KEY is required in production. "
                    f"Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            # Hard-coded fallback ONLY in non-production. We use a random key
            # so devs can't accidentally encrypt anything reproducible with it.
            import secrets
            key_str = secrets.token_urlsafe(32)
            logger.warning(
                "⚠️ ENCRYPTION_MASTER_KEY not set — using a random per-process "
                "dev key. Tenant-encrypted fields will NOT survive a restart. "
                "Set ENCRYPTION_MASTER_KEY for stable dev encryption."
            )
        
        if len(key_str) < 32:
            key_str = key_str.ljust(32, '0')
        
        return key_str.encode()[:32]
    
    @lru_cache(maxsize=100)
    def _derive_tenant_key(self, tenant_id: str, key_version: int = DEFAULT_KEY_VERSION) -> bytes:
        """
        Derive a unique encryption key for a tenant using HKDF.
        
        Args:
            tenant_id: The tenant identifier
            key_version: Key version for rotation support
            
        Returns:
            32-byte derived key encoded as Fernet-compatible base64
        """
        info = f"tenant:{tenant_id}:v{key_version}".encode()
        
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=info,
            backend=default_backend()
        )
        
        derived_key = hkdf.derive(self._master_key)
        fernet_key = base64.urlsafe_b64encode(derived_key)
        
        logger.debug(f"Derived encryption key for tenant: {tenant_id}")
        return fernet_key
    
    def _get_fernet(self, tenant_id: str) -> Fernet:
        """Get Fernet instance for a tenant."""
        key = self._derive_tenant_key(tenant_id, self._key_version)
        return Fernet(key)
    
    def encrypt(self, tenant_id: str, plaintext: str) -> str:
        """
        Encrypt a string value for a specific tenant.
        
        Args:
            tenant_id: The tenant identifier
            plaintext: The value to encrypt
            
        Returns:
            Base64-encoded encrypted string with version prefix
        """
        if not plaintext:
            return plaintext
        
        fernet = self._get_fernet(tenant_id)
        encrypted = fernet.encrypt(plaintext.encode())
        
        versioned = f"v{self._key_version}:{encrypted.decode()}"
        
        logger.debug(f"Encrypted data for tenant {tenant_id}")
        return versioned
    
    def decrypt(self, tenant_id: str, ciphertext: str) -> str:
        """
        Decrypt a string value for a specific tenant.
        
        Args:
            tenant_id: The tenant identifier
            ciphertext: The encrypted value (with version prefix)
            
        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            return ciphertext
        
        if ciphertext.startswith('v'):
            version_end = ciphertext.find(':')
            if version_end > 0:
                version = int(ciphertext[1:version_end])
                encrypted_data = ciphertext[version_end + 1:]
            else:
                version = self._key_version
                encrypted_data = ciphertext
        else:
            version = self._key_version
            encrypted_data = ciphertext
        
        key = self._derive_tenant_key(tenant_id, version)
        fernet = Fernet(key)
        
        try:
            decrypted = fernet.decrypt(encrypted_data.encode())
            logger.debug(f"Decrypted data for tenant {tenant_id}")
            return decrypted.decode()
        except InvalidToken:
            logger.error(f"Failed to decrypt data for tenant {tenant_id} - invalid token")
            raise ValueError("Decryption failed - invalid key or corrupted data")
    
    def encrypt_bytes(self, tenant_id: str, plaintext: bytes) -> bytes:
        """
        Encrypt binary data (e.g., documents) for a tenant.
        
        Args:
            tenant_id: The tenant identifier
            plaintext: Binary data to encrypt
            
        Returns:
            Encrypted binary data with version header
        """
        if not plaintext:
            return plaintext
        
        fernet = self._get_fernet(tenant_id)
        encrypted = fernet.encrypt(plaintext)
        
        version_header = f"v{self._key_version}:".encode()
        return version_header + encrypted
    
    def decrypt_bytes(self, tenant_id: str, ciphertext: bytes) -> bytes:
        """
        Decrypt binary data for a tenant.
        
        Args:
            tenant_id: The tenant identifier
            ciphertext: Encrypted binary data with version header
            
        Returns:
            Decrypted binary data
        """
        if not ciphertext:
            return ciphertext
        
        if ciphertext.startswith(b'v'):
            colon_pos = ciphertext.find(b':')
            if colon_pos > 0:
                version = int(ciphertext[1:colon_pos].decode())
                encrypted_data = ciphertext[colon_pos + 1:]
            else:
                version = self._key_version
                encrypted_data = ciphertext
        else:
            version = self._key_version
            encrypted_data = ciphertext
        
        key = self._derive_tenant_key(tenant_id, version)
        fernet = Fernet(key)
        
        try:
            return fernet.decrypt(encrypted_data)
        except InvalidToken:
            logger.error(f"Failed to decrypt binary data for tenant {tenant_id}")
            raise ValueError("Decryption failed - invalid key or corrupted data")
    
    def rotate_master_key(self, old_master_key: bytes, new_master_key: bytes, 
                         tenant_ids: list, get_encrypted_data_callback) -> dict:
        """
        Rotate master key for specified tenants.
        
        This is a complex operation that should be done during maintenance.
        
        Args:
            old_master_key: The current master key
            new_master_key: The new master key
            tenant_ids: List of tenant IDs to rotate
            get_encrypted_data_callback: Callback to get all encrypted data
            
        Returns:
            Dict with rotation results per tenant
        """
        results = {}
        
        old_service = TenantEncryptionService(old_master_key)
        new_service = TenantEncryptionService(new_master_key)
        new_service._key_version = self._key_version + 1
        
        for tenant_id in tenant_ids:
            try:
                encrypted_items = get_encrypted_data_callback(tenant_id)
                rotated_count = 0
                
                for item in encrypted_items:
                    decrypted = old_service.decrypt(tenant_id, item['value'])
                    new_encrypted = new_service.encrypt(tenant_id, decrypted)
                    item['update_callback'](new_encrypted)
                    rotated_count += 1
                
                results[tenant_id] = {'success': True, 'rotated': rotated_count}
                logger.info(f"Rotated {rotated_count} encrypted items for tenant {tenant_id}")
                
            except Exception as e:
                results[tenant_id] = {'success': False, 'error': str(e)}
                logger.error(f"Key rotation failed for tenant {tenant_id}: {e}")
        
        return results
    
    def mask_value(self, value: str, visible_chars: int = 4, mask_char: str = '*') -> str:
        """
        Mask a sensitive value for display (e.g., PAN: ABCDE****F).
        
        Args:
            value: The sensitive value to mask
            visible_chars: Number of characters to show at start/end
            mask_char: Character to use for masking
            
        Returns:
            Masked string
        """
        if not value or len(value) <= visible_chars * 2:
            return mask_char * len(value) if value else ''
        
        start = value[:visible_chars]
        end = value[-visible_chars:] if visible_chars > 0 else ''
        middle_length = len(value) - (visible_chars * 2)
        
        return f"{start}{mask_char * middle_length}{end}"


_encryption_service: Optional[TenantEncryptionService] = None


def get_encryption_service() -> TenantEncryptionService:
    """Get the singleton encryption service instance."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = TenantEncryptionService()
    return _encryption_service


class EncryptedColumn:
    """
    SQLAlchemy column descriptor for automatic encryption/decryption.
    
    Usage in models:
        class User(db.Model):
            _pan_encrypted = db.Column(db.Text)
            pan_number = EncryptedColumn('_pan_encrypted')
    """
    
    def __init__(self, storage_column: str, mask_on_read: bool = False):
        """
        Initialize encrypted column descriptor.
        
        Args:
            storage_column: Name of the actual column storing encrypted data
            mask_on_read: If True, return masked value instead of decrypted
        """
        self.storage_column = storage_column
        self.mask_on_read = mask_on_read
    
    def __set_name__(self, owner, name):
        self.name = name
    
    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        
        encrypted_value = getattr(obj, self.storage_column, None)
        if not encrypted_value:
            return None
        
        tenant_id = getattr(obj, 'tenant_id', 'live')
        service = get_encryption_service()
        
        try:
            decrypted = service.decrypt(tenant_id, encrypted_value)
            if self.mask_on_read:
                return service.mask_value(decrypted)
            return decrypted
        except Exception as e:
            logger.error(f"Failed to decrypt {self.name}: {e}")
            return None
    
    def __set__(self, obj, value):
        if value is None:
            setattr(obj, self.storage_column, None)
            return
        
        tenant_id = getattr(obj, 'tenant_id', 'live')
        service = get_encryption_service()
        
        encrypted = service.encrypt(tenant_id, value)
        setattr(obj, self.storage_column, encrypted)


def encrypt_for_tenant(tenant_id: str, value: str) -> str:
    """Convenience function to encrypt a value for a tenant."""
    return get_encryption_service().encrypt(tenant_id, value)


def decrypt_for_tenant(tenant_id: str, value: str) -> str:
    """Convenience function to decrypt a value for a tenant."""
    return get_encryption_service().decrypt(tenant_id, value)


def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """Convenience function to mask a sensitive value."""
    return get_encryption_service().mask_value(value, visible_chars)
