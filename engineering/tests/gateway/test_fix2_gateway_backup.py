import asyncio
import stat
import time

import httpx


def auth(raw):
    return {"Authorization": "Bearer " + raw, "Content-Type": "application/json"}


def remove_db(gw):
    for suffix in ("", "-wal", "-shm"):
        path = gw.DB_PATH.with_name(gw.DB_PATH.name + suffix)
        path.unlink(missing_ok=True)


def test_backup_is_encrypted_and_plaintext_key_response_absent(admin_client, admin_headers, gw, key_factory):
    key = key_factory()
    assert not list(gw.LOCAL_DATA_DIR.rglob("key_response.json"))
    with gw.DB_LOCK, gw.connect_db() as conn:
        row = conn.execute("SELECT key_hash,key_prefix FROM api_keys WHERE id=?", (key["id"],)).fetchone()
    assert row["key_hash"] == gw.sha256_text(key["key"])
    assert key["key"] not in (row["key_hash"], row["key_prefix"])
    result = admin_client.post("/admin/api/backup", headers=admin_headers)
    assert result.status_code == 200
    assert result.json()["ok"] is True and result.json()["encrypted"] is True
    files = list(gw.BACKUP_DIR.glob("gateway-*.db.enc"))
    assert files
    assert not files[0].read_bytes().startswith(b"SQLite format 3\x00")
    assert not list(gw.BACKUP_DIR.glob("gateway-*.db"))


def test_backup_requires_passphrase_and_writes_no_plaintext(admin_client, gw, monkeypatch):
    monkeypatch.delenv("LOCAL_GATEWAY_BACKUP_PASSPHRASE", raising=False)
    result = gw.do_backup()
    assert result["ok"] is False and result["error"] == "backup_passphrase_missing"
    assert not list(gw.BACKUP_DIR.glob("gateway-*.db.enc"))
    assert not list(gw.BACKUP_DIR.glob("gateway-*.db"))


def test_database_permissions_and_persistence_metadata(admin_client, admin_headers, gw):
    mode = stat.S_IMODE(gw.DB_PATH.stat().st_mode)
    assert mode & 0o077 == 0
    data = admin_client.get("/admin/api/persistence", headers=admin_headers).json()
    assert data["backup_encryption"] == "aes-256-cbc+pbkdf2"
    assert data["backup_passphrase_source"] == "environment"
    assert data["passphrase_configured"] is True
    assert data["ossfs_chmod_is_not_security_control"] is True
    assert "db_mode" in data and "backup_dir_mode" in data


def test_backup_corruption_falls_back_to_previous_good_and_key_survives(admin_client, gw, key_factory):
    key = key_factory()
    first = gw.do_backup()
    assert first["ok"] is True
    time.sleep(1.1)
    second = gw.do_backup()
    assert second["ok"] is True
    encrypted = sorted(gw.BACKUP_DIR.glob("gateway-*.db.enc"))
    assert len(encrypted) >= 2
    newest = encrypted[-1]
    newest.write_bytes(b"corrupted-not-sqlite")
    remove_db(gw)
    gw.restore_if_missing()
    assert gw.DB_PATH.exists()
    assert gw.STATE["restored_from"] != str(newest)
    with gw.DB_LOCK, gw.connect_db() as conn:
        row = conn.execute("SELECT key_hash FROM api_keys WHERE id=?", (key["id"],)).fetchone()
    assert row and row["key_hash"] == gw.sha256_text(key["key"])


def test_only_corrupted_backup_does_not_restore_and_no_plaintext_fallback(admin_client, gw, key_factory):
    key_factory()
    backup = gw.do_backup()
    assert backup["ok"] is True
    for path in gw.BACKUP_DIR.glob("gateway-*.db.enc"):
        path.write_bytes(b"corrupted")
    legacy = gw.LEGACY_BACKUP_DIR
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "gateway-legacy.db").write_bytes(b"legacy-plaintext-should-not-be-used")
    remove_db(gw)
    gw.restore_if_missing()
    assert not gw.DB_PATH.exists()
    assert gw.STATE["last_restore_error"]


def test_container_rebuild_restore_keeps_existing_key_usable(admin_client, gw, key_factory):
    key = key_factory()
    assert gw.do_backup()["ok"] is True
    remove_db(gw)
    gw.restore_if_missing()
    assert gw.DB_PATH.exists()

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://test") as client:
            r = await client.get("/v1/models", headers=auth(key["key"]))
            assert r.status_code == 200
    asyncio.run(main())


def test_test_and_production_keys_are_separate(admin_client, admin_headers, key_factory):
    prod = key_factory(name="prod", scope="production")
    test = key_factory(name="test", scope="test")
    assert prod["id"] != test["id"]
    assert prod["scope"] == "production" and test["scope"] == "test"
    assert prod["key"].startswith("sk-local-") and test["key"].startswith("sk-test-")
    keys = admin_client.get("/admin/api/keys", headers=admin_headers).json()["keys"]
    by_id = {k["id"]: k for k in keys}
    assert by_id[prod["id"]]["scope"] == "production"
    assert by_id[test["id"]]["scope"] == "test"


def test_rotate_endpoint_invalidates_old_key_only_in_isolated_db(admin_client, admin_headers, gw, key_factory):
    old = key_factory()
    r = admin_client.post("/admin/api/keys/" + old["id"] + "/rotate", headers=admin_headers)
    assert r.status_code == 200 and r.json()["show_once"] is True
    new = r.json()["key"]
    assert new != old["key"]
    assert admin_client.get("/v1/models", headers=auth(old["key"])).status_code == 401
    assert admin_client.get("/v1/models", headers=auth(new)).status_code == 200
