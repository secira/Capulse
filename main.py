from app import app
import routes_payment  # noqa: F401
import routes  # noqa: F401
import routes_broker  # noqa: F401
import routes_mobile  # noqa: F401
import routes_research  # noqa: F401
import routes_daily_signals  # noqa: F401
import routes_behaviour  # noqa: F401
import routes_trader_intelligence  # noqa: F401

from routes_mobile_api import mobile_api
app.register_blueprint(mobile_api)

from routes_workflow import workflow_bp
app.register_blueprint(workflow_bp)

from routes_broker_oauth import broker_oauth
app.register_blueprint(broker_oauth)

from routes_partner_api import partner_api
app.register_blueprint(partner_api)

from routes_chat import chat_bp
app.register_blueprint(chat_bp)

if __name__ == '__main__':
    import os
    # In production (Railway) gunicorn imports `main:app` directly and this
    # block is never executed. Kept only as a dev-local fallback runner.
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1' \
            and os.environ.get('ENVIRONMENT', 'development') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)
