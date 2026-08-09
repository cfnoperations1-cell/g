import os

from crm import create_app

app = create_app()

if __name__ == "__main__":
    # The Werkzeug debugger executes arbitrary code from the browser on any
    # unhandled error, so debug mode must never be the default -- opt in with
    # FLASK_DEBUG=1 while developing. The CRM also binds to localhost only.
    debug = os.environ.get("FLASK_DEBUG", "") == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug)
