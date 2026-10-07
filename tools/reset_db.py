"""Delete all EventFlow data (the local SQLite file, or the PostgreSQL database in DATABASE_URL) so the fresh demo
is recreated on the next start. On Render, use Developer console -> Platform -> Reset demo data instead.

    venv\\Scripts\\python.exe tools\\reset_db.py --yes
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from flask import Flask  # noqa: E402

import db  # noqa: E402


def main():
    if "--yes" not in sys.argv:
        sys.exit("  This deletes every EventFlow table and upload. Re-run with --yes to confirm.")
    app = Flask("eventflow-reset", instance_path=os.path.join(ROOT, "instance"))
    app.config["DATABASE"] = os.environ.get("EVENTFLOW_DB", os.path.join(app.instance_path, "eventflow.db"))
    try:
        db.init_app(app)
    except db.ConfigError as e:
        sys.exit(f"  {e}")
    if db.is_postgres() or os.path.exists(app.config["DATABASE"]):
        with app.app_context():
            db.drop_all()
    print(f"  Cleared: {db.describe()}")
    uploads = os.path.join(app.instance_path, "uploads")
    shutil.rmtree(uploads, ignore_errors=True)
    for f in ("eventflow.db", "eventflow.db-wal", "eventflow.db-shm"):
        try:
            os.remove(os.path.join(app.instance_path, f))
        except OSError:
            pass
    print("  Uploads cleared. Start EventFlow again and the demo data is recreated.")


if __name__ == "__main__":
    main()
