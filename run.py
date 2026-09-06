"""Run the ledger module standalone for development:

    python run.py        ->  http://127.0.0.1:5004/ledger/

Creates a minimal Flask app with only ledger_bp mounted, and a fallback base.html
of its own. When the hub hosts this module the hub supplies base.html and this
file is not used. Port 5004 avoids finance's 5000-5002, jobs' 5003, and the hub's
5010.
"""
from flask import Flask, redirect

from ledger import ledger_bp
from ledger.db import init_db


def create_app() -> Flask:
    app = Flask(__name__, template_folder="fallback_templates")
    app.register_blueprint(ledger_bp, url_prefix="/ledger")
    init_db()

    @app.route("/")
    def _root():
        return redirect("/ledger/")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5004, debug=True)
