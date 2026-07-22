"""
Test Script for RAG + LLM + Vector Database System
Loads sample data and demonstrates intelligent search capabilities
"""

import os
import sys
from datetime import datetime, timedelta, timezone

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from services.vector_service import get_vector_service
from models_vector import PortfolioDocumentChunk, PortfolioKnowledgeBase, ImportedDocumentLog

# Sample broker statement text
SAMPLE_BROKER_STATEMENT = """
ZERODHA HOLDINGS STATEMENT
Date: 15-Dec-2024
Account: ZD12345

EQUITY HOLDINGS:
-------------------
RELIANCE INDUSTRIES LTD (RELIANCE)
Quantity: 100 shares
Average Buy Price: ₹2,500.50
Current Market Price: ₹2,650.00
Unrealized P&L: ₹14,950.00 (+5.98%)
ISIN: INE002A01018

TCS LIMITED (TCS)
Quantity: 50 shares
Average Buy Price: ₹3,200.00
Current Market Price: ₹3,450.00
Unrealized P&L: ₹12,500.00 (+7.81%)
ISIN: INE467B01029

HDFC BANK LTD (HDFCBANK)
Quantity: 75 shares
Average Buy Price: ₹1,550.00
Current Market Price: ₹1,620.00
Unrealized P&L: ₹5,250.00 (+4.52%)
ISIN: INE040A01034

INFOSYS LIMITED (INFY)
Quantity: 60 shares
Average Buy Price: ₹1,450.00
Current Market Price: ₹1,480.00
Unrealized P&L: ₹1,800.00 (+2.07%)
ISIN: INE009A01021

Total Portfolio Value: ₹423,200.00
Total Unrealized P&L: ₹34,500.00 (+8.88%)
"""

SAMPLE_MUTUAL_FUND_STATEMENT = """
CONSOLIDATED ACCOUNT STATEMENT (CAS)
Period: 01-Jan-2024 to 31-Dec-2024

HDFC EQUITY FUND - DIRECT PLAN - GROWTH
Fund House: HDFC Asset Management
Folio: 12345/67
Units: 250.456
NAV: ₹850.50
Current Value: ₹213,012.77
Total Investment: ₹180,000.00
Absolute Return: 18.34%

SBI BLUECHIP FUND - DIRECT PLAN - GROWTH
Fund House: SBI Mutual Fund
Folio: 98765/43
Units: 180.234
NAV: ₹975.25
Current Value: ₹175,753.21
Total Investment: ₹160,000.00
Absolute Return: 9.85%

ICICI PRUDENTIAL TECHNOLOGY FUND - DIRECT PLAN - GROWTH
Fund House: ICICI Prudential
Folio: 54321/98
Units: 120.567
NAV: ₹1,125.75
Current Value: ₹135,713.17
Total Investment: ₹110,000.00
Absolute Return: 23.38%
"""

SAMPLE_RESEARCH_REPORT = """
EQUITY RESEARCH REPORT
Stock: RELIANCE INDUSTRIES LTD
Date: 20-Dec-2024
Analyst: Capulse Research

RECOMMENDATION: BUY
Target Price: ₹3,000
Current Price: ₹2,650
Upside Potential: 13.21%

KEY HIGHLIGHTS:
- Strong Q3 FY2024 results with 15% YoY revenue growth
- Jio subscriber base crossed 450 million
- Retail segment showing robust performance
- Debt reduction on track
- New energy projects gaining momentum

FINANCIALS (Q3 FY2024):
Revenue: ₹2,35,000 crores (+15% YoY)
EBITDA: ₹42,500 crores (+12% YoY)
Net Profit: ₹18,500 crores (+10% YoY)
EPS: ₹27.50

VALUATION:
P/E Ratio: 25.5x
EV/EBITDA: 12.8x
Price to Book: 2.1x

RISKS:
- Oil price volatility
- Regulatory changes in telecom sector
- Competition in retail segment

CONCLUSION:
Reliance Industries remains our top pick in the large-cap space with strong fundamentals across all business segments. We maintain BUY rating with target price of ₹3,000.
"""

SAMPLE_TRADING_INSIGHT = """
TRADING SIGNAL ALERT
Generated: 22-Dec-2024 10:30 AM

STOCK: TCS LIMITED (TCS)
SIGNAL: STRONG BUY
Entry Zone: ₹3,420 - ₹3,450
Target 1: ₹3,550
Target 2: ₹3,650
Stop Loss: ₹3,350

TECHNICAL ANALYSIS:
- Stock broke out of consolidation pattern
- RSI at 58 (neutral to bullish)
- MACD showing bullish crossover
- Volume spike on breakout
- 50-day MA support at ₹3,400

FUNDAMENTAL SUPPORT:
- Strong Q3 earnings expected
- TCV (Total Contract Value) guidance raised
- Cloud migration tailwinds
- Sector rotation favoring IT stocks

RISK-REWARD RATIO: 1:3
CONFIDENCE LEVEL: 85%

STRATEGY:
Buy 50% position at ₹3,420-3,430
Add 30% on dip to ₹3,400
Final 20% on confirmation above ₹3,470
"""


def create_test_user():
    """Create or get test user"""
    from models import User, PricingPlan, SubscriptionStatus
    from werkzeug.security import generate_password_hash
    
    with app.app_context():
        # Check if test user exists
        user = User.query.filter_by(email='test@targetcapital.com').first()
        
        if not user:
            print("Creating test user...")
            user = User(
                username='testuser',
                email='test@targetcapital.com',
                password_hash=generate_password_hash('test123'),
                pricing_plan=PricingPlan.TARGET_PRO,
                subscription_status=SubscriptionStatus.ACTIVE,
                is_verified=True
            )
            db.session.add(user)
            db.session.commit()
            print(f"✅ Test user created (ID: {user.id})")
        else:
            print(f"✅ Using existing test user (ID: {user.id})")
        
        return user.id


def load_test_data():
    """Load test data into vector database"""
    print("=" * 80)
    print("LOADING TEST DATA INTO RAG + LLM + VECTOR DATABASE")
    print("=" * 80)
    
    # Create or get test user
    user_id = create_test_user()
    
    with app.app_context():
        vector_service = get_vector_service()
        
        # 1. Load Broker Statement
        print("\n1. Processing Broker Statement...")
        broker_chunks = [
            SAMPLE_BROKER_STATEMENT[:500],
            SAMPLE_BROKER_STATEMENT[500:1000],
            SAMPLE_BROKER_STATEMENT[1000:]
        ]
        
        broker_chunk_ids = vector_service.store_document_chunks(
            user_id=user_id,
            document_type='broker_statement',
            chunks=broker_chunks,
            asset_class='equities',
            source='Zerodha',
            metadata={'date': '2024-12-15', 'account': 'ZD12345'}
        )
        print(f"   ✅ Created {len(broker_chunk_ids)} broker statement chunks")
        
        # 2. Load Mutual Fund Statement
        print("\n2. Processing Mutual Fund Statement...")
        mf_chunks = [
            SAMPLE_MUTUAL_FUND_STATEMENT[:400],
            SAMPLE_MUTUAL_FUND_STATEMENT[400:800],
            SAMPLE_MUTUAL_FUND_STATEMENT[800:]
        ]
        
        mf_chunk_ids = vector_service.store_document_chunks(
            user_id=user_id,
            document_type='cas_statement',
            chunks=mf_chunks,
            asset_class='mutual_funds',
            source='CAMS',
            metadata={'period': '2024', 'type': 'CAS'}
        )
        print(f"   ✅ Created {len(mf_chunk_ids)} mutual fund chunks")
        
        # 3. Load Research Report
        print("\n3. Processing Research Report...")
        research_chunks = [
            SAMPLE_RESEARCH_REPORT[:600],
            SAMPLE_RESEARCH_REPORT[600:]
        ]
        
        research_chunk_ids = vector_service.store_document_chunks(
            user_id=user_id,
            document_type='research_report',
            chunks=research_chunks,
            asset_class='equities',
            source='Capulse Research',
            metadata={'stock': 'RELIANCE', 'date': '2024-12-20', 'analyst': 'Research Team'}
        )
        print(f"   ✅ Created {len(research_chunk_ids)} research report chunks")
        
        # 4. Load Trading Signal
        print("\n4. Processing Trading Signal...")
        signal_chunks = [
            SAMPLE_TRADING_INSIGHT[:400],
            SAMPLE_TRADING_INSIGHT[400:]
        ]
        
        signal_chunk_ids = vector_service.store_document_chunks(
            user_id=user_id,
            document_type='trading_signal',
            chunks=signal_chunks,
            asset_class='equities',
            source='Capulse AI',
            metadata={'stock': 'TCS', 'signal': 'BUY', 'timestamp': '2024-12-22T10:30:00'}
        )
        print(f"   ✅ Created {len(signal_chunk_ids)} trading signal chunks")
        
        # 5. Create Knowledge Base Entries
        print("\n5. Creating Knowledge Base Entries...")
        
        # Equity holding knowledge
        kb_id1 = vector_service.create_knowledge_item(
            user_id=user_id,
            knowledge_type='holding',
            title='RELIANCE Holdings - Zerodha',
            content='Currently holding 100 shares of RELIANCE at avg price ₹2,500.50. Current value ₹2,65,000 with unrealized profit of ₹14,950 (5.98%)',
            asset_class='equities',
            asset_symbol='RELIANCE',
            structured_data={
                'quantity': 100,
                'avg_price': 2500.50,
                'current_price': 2650.00,
                'unrealized_pnl': 14950.00,
                'pnl_percentage': 5.98
            },
            source_chunk_ids=broker_chunk_ids
        )
        print(f"   ✅ Created equity holding knowledge (ID: {kb_id1})")
        
        # Research recommendation
        kb_id2 = vector_service.create_knowledge_item(
            user_id=user_id,
            knowledge_type='recommendation',
            title='RELIANCE - BUY Recommendation',
            content='Capulse recommends BUY on RELIANCE with target price ₹3,000 (13.21% upside). Strong Q3 results, Jio growth, retail performance.',
            asset_class='equities',
            asset_symbol='RELIANCE',
            structured_data={
                'recommendation': 'BUY',
                'target_price': 3000,
                'current_price': 2650,
                'upside': 13.21,
                'confidence': 'HIGH'
            },
            source_chunk_ids=research_chunk_ids
        )
        print(f"   ✅ Created research recommendation (ID: {kb_id2})")
        
        # Trading signal
        kb_id3 = vector_service.create_knowledge_item(
            user_id=user_id,
            knowledge_type='trading_signal',
            title='TCS - STRONG BUY Signal',
            content='AI Trading Signal: STRONG BUY on TCS. Entry ₹3,420-3,450. Targets: ₹3,550/₹3,650. Stop Loss: ₹3,350. Technical breakout with bullish indicators.',
            asset_class='equities',
            asset_symbol='TCS',
            structured_data={
                'signal': 'STRONG_BUY',
                'entry_zone': [3420, 3450],
                'targets': [3550, 3650],
                'stop_loss': 3350,
                'confidence': 85,
                'risk_reward': 3.0
            },
            source_chunk_ids=signal_chunk_ids
        )
        print(f"   ✅ Created trading signal (ID: {kb_id3})")
        
        # 6. Create Import Log
        print("\n6. Creating Import Logs...")
        import_log = ImportedDocumentLog(
            user_id=user_id,
            filename='test_broker_statement.pdf',
            file_type='pdf',
            file_size=1024,
            import_type='document_upload',
            asset_class='equities',
            source='Zerodha',
            status='completed',
            records_imported=4,
            chunks_created=len(broker_chunk_ids),
            knowledge_items_created=1,
            processing_time_seconds=2.5,
            llm_tokens_used=1500,
            llm_cost=0.003,
            completed_at=datetime.now(timezone.utc)
        )
        db.session.add(import_log)
        db.session.commit()
        print(f"   ✅ Created import log (ID: {import_log.id})")
        
        print("\n" + "=" * 80)
        print("✅ TEST DATA LOADED SUCCESSFULLY!")
        print("=" * 80)
        
        return {
            'broker_chunk_ids': broker_chunk_ids,
            'mf_chunk_ids': mf_chunk_ids,
            'research_chunk_ids': research_chunk_ids,
            'signal_chunk_ids': signal_chunk_ids,
            'knowledge_ids': [kb_id1, kb_id2, kb_id3]
        }


def test_rag_search():
    """Test RAG search functionality"""
    print("\n" + "=" * 80)
    print("TESTING RAG SEARCH CAPABILITIES")
    print("=" * 80)
    
    with app.app_context():
        vector_service = get_vector_service()
        user_id = 1
        
        # Test 1: Search for equity holdings
        print("\n1. SEARCH: 'What stocks do I own?'")
        print("-" * 40)
        results = vector_service.search_knowledge_base(
            user_id=user_id,
            query="What stocks do I own?",
            knowledge_type='holding',
            top_k=5
        )
        
        for i, result in enumerate(results, 1):
            print(f"\n   Result {i} (Similarity: {result['similarity_score']:.3f})")
            print(f"   Title: {result['title']}")
            print(f"   Content: {result['content'][:100]}...")
        
        # Test 2: Search for buy recommendations
        print("\n\n2. SEARCH: 'Which stocks should I buy?'")
        print("-" * 40)
        results = vector_service.search_knowledge_base(
            user_id=user_id,
            query="Which stocks should I buy?",
            knowledge_type='recommendation',
            top_k=5
        )
        
        for i, result in enumerate(results, 1):
            print(f"\n   Result {i} (Similarity: {result['similarity_score']:.3f})")
            print(f"   Title: {result['title']}")
            print(f"   Data: {result['structured_data']}")
        
        # Test 3: Search for trading signals
        print("\n\n3. SEARCH: 'Show me trading signals for TCS'")
        print("-" * 40)
        results = vector_service.search_knowledge_base(
            user_id=user_id,
            query="Show me trading signals for TCS",
            top_k=5
        )
        
        for i, result in enumerate(results, 1):
            print(f"\n   Result {i} (Similarity: {result['similarity_score']:.3f})")
            print(f"   Title: {result['title']}")
            print(f"   Signal Data: {result['structured_data']}")
        
        # Test 4: Search document chunks
        print("\n\n4. SEARCH CHUNKS: 'RELIANCE earnings and financials'")
        print("-" * 40)
        results = vector_service.search_document_chunks(
            user_id=user_id,
            query="RELIANCE earnings and financials",
            document_type='research_report',
            top_k=3
        )
        
        for i, result in enumerate(results, 1):
            print(f"\n   Chunk {i} (Similarity: {result['similarity_score']:.3f})")
            print(f"   Source: {result['source']}")
            print(f"   Content: {result['chunk_text'][:150]}...")
        
        # Test 5: Portfolio analysis query
        print("\n\n5. SEARCH: 'Analyze my portfolio performance'")
        print("-" * 40)
        results = vector_service.search_knowledge_base(
            user_id=user_id,
            query="Analyze my portfolio performance profit loss returns",
            top_k=5
        )
        
        for i, result in enumerate(results, 1):
            print(f"\n   Result {i} (Similarity: {result['similarity_score']:.3f})")
            print(f"   Type: {result['knowledge_type']}")
            print(f"   Title: {result['title']}")
        
        print("\n" + "=" * 80)
        print("✅ RAG SEARCH TESTS COMPLETED!")
        print("=" * 80)


def test_database_stats():
    """Show database statistics"""
    print("\n" + "=" * 80)
    print("VECTOR DATABASE STATISTICS")
    print("=" * 80)
    
    with app.app_context():
        total_chunks = PortfolioDocumentChunk.query.count()
        total_knowledge = PortfolioKnowledgeBase.query.count()
        total_imports = ImportedDocumentLog.query.count()
        
        print(f"\n📊 Total Document Chunks: {total_chunks}")
        print(f"📚 Total Knowledge Items: {total_knowledge}")
        print(f"📁 Total Import Logs: {total_imports}")
        
        # Breakdown by type
        print("\n📑 Chunks by Document Type:")
        from sqlalchemy import func
        chunk_types = db.session.query(
            PortfolioDocumentChunk.document_type,
            func.count(PortfolioDocumentChunk.id)
        ).group_by(PortfolioDocumentChunk.document_type).all()
        
        for doc_type, count in chunk_types:
            print(f"   - {doc_type}: {count}")
        
        print("\n💡 Knowledge by Type:")
        kb_types = db.session.query(
            PortfolioKnowledgeBase.knowledge_type,
            func.count(PortfolioKnowledgeBase.id)
        ).group_by(PortfolioKnowledgeBase.knowledge_type).all()
        
        for kb_type, count in kb_types:
            print(f"   - {kb_type}: {count}")
        
        print("\n" + "=" * 80)


if __name__ == "__main__":
    print("\n🚀 STARTING RAG + LLM + VECTOR DATABASE TEST")
    print("=" * 80)
    
    try:
        # Load test data
        data_ids = load_test_data()
        
        # Test RAG search
        test_rag_search()
        
        # Show stats
        test_database_stats()
        
        print("\n✅ ALL TESTS COMPLETED SUCCESSFULLY!")
        print("\nThe RAG + LLM + Vector Database system is now ready for:")
        print("  • Research: Intelligent document search and insights")
        print("  • Portfolio Analysis: Holdings analysis and recommendations")
        print("  • Trading: Signal generation and strategy recommendations")
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
