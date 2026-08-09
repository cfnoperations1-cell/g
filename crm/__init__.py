from pathlib import Path

from flask import Flask

import config


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = config.FLASK_SECRET_KEY

    from db import init_db

    init_db()

    from crm.routes import bp as leads_bp

    app.register_blueprint(leads_bp)

    return app
