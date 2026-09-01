import json
import os
import py_compile
from pathlib import Path

import db


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_STATES = REPO_ROOT / "project_states.json"


def test_app_py_compiles():
    py_compile.compile(str(REPO_ROOT / "app.py"), doraise=True)


def test_google_pay_stays_disabled_after_fresh_sqlite(tmp_path, monkeypatch):
    """Simulate /var/data: disable google_pay, sync, new sqlite, init+restore keeps it off."""
    data_dir = tmp_path / "var_data"
    data_dir.mkdir()
    db_path = str(data_dir / "crowdfund.db")

    monkeypatch.setattr(db, "VAR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("ADMIN_EMAIL", "yacov@drori.org")
    monkeypatch.setenv("ADMIN_INITIAL_PASSWORD", "A-very-strong-admin-password-2026!")

    original_states = REPO_STATES.read_text(encoding="utf-8") if REPO_STATES.exists() else None
    try:
        db.DB_PATH = db.resolve_db_path()
        assert os.path.abspath(db.DB_PATH) == os.path.abspath(db_path)
        parent = os.path.dirname(os.path.abspath(db.DB_PATH))
        assert os.path.isdir(parent)

        db.init_db()
        db.seed_db()

        conn = db.get_db()
        conn.execute(
            "UPDATE payment_gateways SET is_enabled = 0 WHERE gateway_key = 'google_pay'"
        )
        conn.commit()
        before = conn.execute(
            "SELECT is_enabled FROM payment_gateways WHERE gateway_key = 'google_pay'"
        ).fetchone()
        assert before is not None
        assert int(before["is_enabled"]) == 0
        db.sync_project_states(conn)
        conn.close()

        json_path = db.get_project_states_path()
        assert os.path.abspath(json_path) == os.path.abspath(str(data_dir / "project_states.json"))
        assert os.path.exists(json_path)
        snapshot = json.loads(Path(json_path).read_text(encoding="utf-8"))
        gateways = {g["gateway_key"]: g for g in snapshot["_payment_gateways"]}
        assert int(gateways["google_pay"]["is_enabled"]) == 0

        os.remove(db.DB_PATH)
        assert not os.path.exists(db.DB_PATH)

        db.init_db()
        db.seed_db()

        conn = db.get_db()
        after = conn.execute(
            "SELECT is_enabled, account_identifier, sandbox_mode, instructions "
            "FROM payment_gateways WHERE gateway_key = 'google_pay'"
        ).fetchone()
        conn.close()
        assert after is not None
        assert int(after["is_enabled"]) == 0
    finally:
        if original_states is not None:
            REPO_STATES.write_text(original_states, encoding="utf-8")
