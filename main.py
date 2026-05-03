from app import app
import routes_payment  # noqa: F401
import routes  # noqa: F401
import routes_broker  # noqa: F401
import routes_mobile  # noqa: F401
import routes_research  # noqa: F401
import routes_daily_signals  # noqa: F401
import routes_behaviour  # noqa: F401

from routes_mobile_api import mobile_api
app.register_blueprint(mobile_api)

from routes_workflow import workflow_bp
app.register_blueprint(workflow_bp)

from routes_broker_oauth import broker_oauth
app.register_blueprint(broker_oauth)

from routes_partner_api import partner_api
app.register_blueprint(partner_api)

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
