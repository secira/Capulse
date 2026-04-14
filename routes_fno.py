"""
F&O Analysis Routes — NIFTY Options Engine
"""

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
import logging

logger = logging.getLogger(__name__)

fno_bp = Blueprint('fno', __name__, url_prefix='/dashboard/fno')


@fno_bp.route('/')
@login_required
def fno_landing():
    return render_template('dashboard/fno_nifty.html')


@fno_bp.route('/nifty')
@login_required
def fno_nifty():
    return render_template('dashboard/fno_nifty.html')


@fno_bp.route('/api/analysis')
@login_required
def fno_analysis_api():
    try:
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine()
        analysis = engine.generate_analysis()
        return jsonify({'success': True, 'data': analysis})
    except Exception as e:
        logger.error(f"F&O analysis error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@fno_bp.route('/api/indices')
@login_required
def fno_indices_api():
    try:
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine()
        indices = engine.get_market_indices()
        return jsonify({'success': True, 'data': indices})
    except Exception as e:
        logger.error(f"Indices fetch error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
