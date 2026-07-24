"""
Vector Database Models for RAG-powered Portfolio Analysis
Stores embeddings and document chunks for intelligent search and analysis
"""

from db_instance import db
from datetime import datetime
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import Text


class PortfolioDocumentChunk(db.Model):
    """
    Store document chunks with embeddings for RAG retrieval
    Used for: Research, Portfolio Analysis, Trading insights
    """
    __tablename__ = 'portfolio_document_chunks'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    
    # Document metadata
    document_type = db.Column(db.String(50), nullable=False, index=True)  # broker_statement, bank_statement, research_report, trading_note
    asset_class = db.Column(db.String(50), nullable=True, index=True)  # equities, mutual_funds, etc.
    source = db.Column(db.String(100), nullable=True)  # Zerodha, HDFC Bank, User Upload
    document_date = db.Column(db.Date, nullable=True, index=True)
    
    # Chunk content
    chunk_text = db.Column(Text, nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)  # Position in document
    chunk_size = db.Column(db.Integer, nullable=False)  # Character count
    
    # Embedding (stored as JSON array for compatibility)
    # Using 1536 dimensions for OpenAI ada-002 embeddings
    embedding = db.Column(db.JSON, nullable=True)  # [0.123, -0.456, ...] 1536 floats
    
    # Metadata for filtering (renamed to avoid SQLAlchemy reserved word)
    document_metadata = db.Column(db.JSON, nullable=True)  # Flexible storage for additional data
    
    # Tags for quick filtering
    tags = db.Column(ARRAY(db.String), nullable=True, index=True)  # ['holdings', 'equity', 'RELIANCE']
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='document_chunks')
    
    def __repr__(self):
        return f'<DocumentChunk {self.id}: {self.document_type} - {self.chunk_text[:50]}>'


class PortfolioKnowledgeBase(db.Model):
    """
    Store extracted and structured knowledge from portfolio documents
    This is the processed, actionable data extracted from chunks
    """
    __tablename__ = 'portfolio_knowledge_base'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    
    # Knowledge metadata
    knowledge_type = db.Column(db.String(50), nullable=False, index=True)  # holding, transaction, insight, recommendation
    asset_class = db.Column(db.String(50), nullable=True, index=True)
    asset_symbol = db.Column(db.String(50), nullable=True, index=True)  # RELIANCE, HDFCBANK
    
    # Extracted knowledge
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(Text, nullable=False)
    structured_data = db.Column(db.JSON, nullable=True)  # Extracted structured information
    
    # Source reference
    source_chunk_ids = db.Column(ARRAY(db.Integer), nullable=True)  # References to PortfolioDocumentChunk
    source_document = db.Column(db.String(200), nullable=True)
    confidence_score = db.Column(db.Float, nullable=True)  # LLM confidence (0-1)
    
    # For RAG retrieval
    embedding = db.Column(db.JSON, nullable=True)  # Title + content embedding
    
    # Categorization
    category = db.Column(db.String(100), nullable=True, index=True)  # holdings, trades, analysis, research
    tags = db.Column(ARRAY(db.String), nullable=True, index=True)
    
    # Temporal relevance
    relevant_date = db.Column(db.Date, nullable=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=True)  # For time-sensitive insights
    
    # Usage tracking
    used_in_analysis = db.Column(db.Boolean, default=False)
    used_in_research = db.Column(db.Boolean, default=False)
    used_in_trading = db.Column(db.Boolean, default=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='knowledge_base')
    
    def __repr__(self):
        return f'<PortfolioKnowledge {self.id}: {self.knowledge_type} - {self.title}>'


class ImportedDocumentLog(db.Model):
    """
    Track all imported documents for audit and reprocessing
    """
    __tablename__ = 'imported_document_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    
    # Document info
    filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)  # pdf, excel, csv
    file_size = db.Column(db.Integer, nullable=True)  # bytes
    file_hash = db.Column(db.String(64), nullable=True, unique=True)  # SHA-256 for deduplication
    
    # Import details
    import_type = db.Column(db.String(50), nullable=False)  # broker_api, bank_api, document_upload
    asset_class = db.Column(db.String(50), nullable=True)
    source = db.Column(db.String(100), nullable=True)  # Zerodha, HDFC, etc.
    
    # Processing status
    status = db.Column(db.String(20), default='processing')  # processing, completed, failed
    records_imported = db.Column(db.Integer, default=0)
    chunks_created = db.Column(db.Integer, default=0)
    knowledge_items_created = db.Column(db.Integer, default=0)
    
    # Error tracking
    error_message = db.Column(Text, nullable=True)
    retry_count = db.Column(db.Integer, default=0)
    
    # Processing metadata
    processing_time_seconds = db.Column(db.Float, nullable=True)
    llm_tokens_used = db.Column(db.Integer, nullable=True)
    llm_cost = db.Column(db.Float, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    user = db.relationship('User', backref='import_logs')
    
    def __repr__(self):
        return f'<ImportLog {self.id}: {self.filename} - {self.status}>'


class PortfolioAssetEmbedding(db.Model):
    """
    Store vector embeddings for user's portfolio assets for semantic search and AI analysis
    Automatically populated when assets are added/synced from brokers or manual entry
    """
    __tablename__ = 'portfolio_asset_embeddings'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    
    # Asset identification
    asset_type = db.Column(db.String(50), nullable=False, index=True)  # equities, mutual_funds, fixed_deposits, etc.
    asset_symbol = db.Column(db.String(50), nullable=True, index=True)  # Stock symbol, fund name, etc.
    asset_name = db.Column(db.String(200), nullable=False)  # Full name
    asset_category = db.Column(db.String(50), nullable=True, index=True)  # equity, debt, commodities
    
    # Reference to original record
    source_table = db.Column(db.String(100), nullable=False)  # broker_holdings, manual_equity_holdings, etc.
    source_record_id = db.Column(db.Integer, nullable=False, index=True)  # ID in source table
    broker_account_id = db.Column(db.Integer, db.ForeignKey('user_brokers.id'), nullable=True, index=True)  # If from broker
    
    # Asset description for embedding (rich text combining all relevant info)
    asset_description = db.Column(Text, nullable=False)  # "RELIANCE equity stock, 50 shares at avg price ₹2500..."
    
    # Key financial data (for filtering)
    quantity = db.Column(db.Float, nullable=True)
    current_value = db.Column(db.Float, nullable=True)
    purchase_value = db.Column(db.Float, nullable=True)
    current_price = db.Column(db.Float, nullable=True)
    
    # Vector embedding (1536 dimensions for OpenAI ada-002)
    embedding = db.Column(db.JSON, nullable=True)  # [0.123, -0.456, ...] 1536 floats
    
    # Metadata (flexible storage for asset-specific details)
    asset_metadata = db.Column(db.JSON, nullable=True)  # sector, industry, risk_level, maturity_date, etc.
    
    # Tags for quick semantic grouping
    tags = db.Column(ARRAY(db.String), nullable=True, index=True)  # ['technology', 'large-cap', 'high-risk']
    
    # Status
    is_active = db.Column(db.Boolean, default=True, index=True)  # False if asset sold/removed
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_synced = db.Column(db.DateTime, default=datetime.utcnow)  # Last time data was updated
    
    # Relationships
    user = db.relationship('User', backref='asset_embeddings')
    
    def __repr__(self):
        return f'<PortfolioAssetEmbedding {self.id}: {self.asset_name} ({self.asset_type})>'


class VectorSearchCache(db.Model):
    """
    Cache vector search results for common queries
    Improves performance for frequently asked questions
    """
    __tablename__ = 'vector_search_cache'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    
    # Query
    query_text = db.Column(Text, nullable=False)
    query_hash = db.Column(db.String(64), nullable=False, index=True)  # For quick lookup
    query_embedding = db.Column(db.JSON, nullable=True)
    
    # Results
    result_chunk_ids = db.Column(ARRAY(db.Integer), nullable=True)
    result_knowledge_ids = db.Column(ARRAY(db.Integer), nullable=True)
    result_data = db.Column(db.JSON, nullable=True)  # Cached processed results
    
    # Cache metadata
    hit_count = db.Column(db.Integer, default=0)
    last_accessed = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)  # Cache expiry
    
    # Relationships
    user = db.relationship('User', backref='search_cache')
    
    def __repr__(self):
        return f'<SearchCache {self.id}: {self.query_text[:50]}>'
