import time

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from server.provider import ProviderFailure
from server.worker import Worker
from server.workflow import build_graph
from tests.test_api import enqueue


async def process(client, provider):
    db = client.app.state.db
    data = client.app.state.settings.data_dir
    async with AsyncSqliteSaver.from_conn_string(str(data / "test-checkpoints.sqlite3")) as saver:
        graph = build_graph(db, provider, data / "outputs", saver)
        worker = Worker(db, graph, data / "outputs")
        row = db.claim(worker.owner)
        assert row
        await worker.process(row)
    return db.one("SELECT * FROM jobs WHERE id=?", (row["id"],))


async def test_real_graph_saves_private_image_and_prompt_trace(client, user, provider):
    job = enqueue(client).json()
    row = await process(client, provider)
    assert row["status"] == "succeeded"
    assert row["enhanced_prompt"] == "Refined: " + row["prompt"] + "\nVisual direction: Natural photorealism, authentic textures, believable light and balanced composition."
    assert provider.prompt == row["enhanced_prompt"]
    assert provider.calls == 1
    details = client.get("/api/jobs/" + job["id"]).json()
    stages = [event["stage"] for event in details["events"]]
    assert all(
        stage in stages
        for stage in [
            "queued",
            "validating",
            "enhancing",
            "moderating",
            "generating",
            "saving",
            "succeeded",
        ]
    )
    assert client.get(details["image_url"]).status_code == 200
    assert row["provider_request_id"] == "request-test-only"
    assert client.get("/api/jobs/" + job["id"] + "/events").status_code == 200


async def test_edit_routes_reference_to_provider(client, user, provider, png):
    asset = client.post("/api/assets", content=png, headers={"Content-Type": "image/png"}).json()
    enqueue(client, reference_id=asset["id"], enhance=False)
    row = await process(client, provider)
    assert row["status"] == "succeeded"
    assert provider.reference.is_file()
    assert row["enhanced_prompt"] is None


async def test_content_rejected_before_billable_generation_and_refunded(client, user, provider):
    provider.allowed = False
    enqueue(client)
    row = await process(client, provider)
    assert row["status"] == "failed" and row["error_code"] == "content_blocked"
    assert provider.calls == 0
    assert client.get("/api/auth/me").json()["quota"]["used"] == 0


async def test_uncertain_timeout_has_no_automatic_retry_or_refund(client, user, provider):
    provider.failure = ProviderFailure("provider_unknown", "Result unknown", uncertain=True)
    enqueue(client)
    row = await process(client, provider)
    assert row["status"] == "uncertain"
    assert provider.calls == 1
    assert client.app.state.db.claim("another-worker") is None
    assert client.get("/api/auth/me").json()["quota"]["used"] == 1


async def test_checkpoint_resume_after_storage_crash_does_not_generate_twice(
    client, user, provider, monkeypatch
):
    enqueue(client)
    db = client.app.state.db
    original_complete = db.complete
    calls = 0

    def fail_once(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("test-only simulated storage interruption")
        return original_complete(*args)

    monkeypatch.setattr(db, "complete", fail_once)
    row = await process(client, provider)
    assert row["status"] == "queued"
    # Reopen the on-disk saver, simulating a process restart.
    row = await process(client, provider)
    assert row["status"] == "succeeded"
    assert provider.calls == 1
    assert len(db.all("SELECT * FROM assets")) == 1
    original_complete(
        row["id"], db.one("SELECT path FROM assets WHERE id=?", (row["id"],))["path"], 64, 64
    )
    assert len(db.all("SELECT * FROM assets")) == 1


def test_expired_lease_before_and_during_provider_call(client, user):
    safe = enqueue(client).json()
    unknown = enqueue(client, prompt="A tall mountain beneath a planet").json()
    db = client.app.state.db
    db.execute(
        "UPDATE jobs SET status='moderating',lease_until=? WHERE id=?",
        (time.time() - 1, safe["id"]),
    )
    db.execute(
        "UPDATE jobs SET status='generating',lease_until=?,provider_started_at=? WHERE id=?",
        (time.time() - 1, time.time(), unknown["id"]),
    )
    db.recover_expired(client.app.state.settings.data_dir / "outputs")
    assert db.one("SELECT status FROM jobs WHERE id=?", (safe["id"],))["status"] == "queued"
    assert db.one("SELECT status FROM jobs WHERE id=?", (unknown["id"],))["status"] == "uncertain"


def test_expired_recovery_does_not_probe_storage_before_provider_call(client, user):
    safe = enqueue(client).json()
    unknown = enqueue(client, prompt="A tall mountain beneath a planet").json()
    db = client.app.state.db
    db.execute(
        "UPDATE jobs SET status='moderating',lease_until=? WHERE id=?",
        (time.time() - 1, safe["id"]),
    )
    db.execute(
        "UPDATE jobs SET status='generating',lease_until=?,provider_started_at=? WHERE id=?",
        (time.time() - 1, time.time(), unknown["id"]),
    )

    class BrokenRemoteStore:
        def output_key(self, user_id, job_id):
            return f"users/{user_id}/outputs/{job_id}.png"

        def exists(self, key):
            raise RuntimeError("test-only storage outage")

    db.recover_expired(BrokenRemoteStore())
    assert db.one("SELECT status FROM jobs WHERE id=?", (safe["id"],))["status"] == "queued"
    assert db.one("SELECT status FROM jobs WHERE id=?", (unknown["id"],))["status"] == "uncertain"


async def test_expired_lease_with_durable_image_resumes_safely(client, user, provider, png):
    job = enqueue(client).json()
    data = client.app.state.settings.data_dir
    (data / "outputs" / (job["id"] + ".png")).write_bytes(png)
    client.app.state.db.execute(
        "UPDATE jobs SET status='generating',lease_until=?,provider_started_at=? WHERE id=?",
        (time.time() - 1, time.time(), job["id"]),
    )
    client.app.state.db.recover_expired(data / "outputs")
    row = await process(client, provider)
    assert row["status"] == "succeeded"
    assert provider.calls == 0


async def test_worker_records_failure_when_error_handler_cannot_probe_remote_storage(client, user):
    enqueue(client)
    db = client.app.state.db

    class BrokenRemoteStore:
        def output_key(self, user_id, job_id):
            return f"users/{user_id}/outputs/{job_id}.png"

        def exists(self, key):
            raise RuntimeError("test-only storage outage")

    class FailingGraph:
        async def aget_state(self, config):
            return type("Snapshot", (), {"values": {}, "next": ()})()

        async def ainvoke(self, value, config, durability):
            raise RuntimeError("test-only provider failure")

    worker = Worker(
        db,
        FailingGraph(),
        client.app.state.settings.data_dir / "outputs",
        asset_store=BrokenRemoteStore(),
    )
    await worker.process(db.claim(worker.owner))
    row = db.one("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1")
    assert row["status"] == "failed"
    assert row["error_code"] == "workflow_error"


def test_archive_recovery_has_a_finite_limit(client, user, png):
    job = enqueue(client).json()
    db = client.app.state.db
    data = client.app.state.settings.data_dir
    (data / "outputs" / (job["id"] + ".png")).write_bytes(png)
    db.execute("UPDATE jobs SET provider_started_at=? WHERE id=?", (time.time(), job["id"]))
    for _ in range(3):
        db.recover(job["id"])
    assert db.one("SELECT status FROM jobs WHERE id=?", (job["id"],))["status"] == "uncertain"
    assert db.one("SELECT recovery_count FROM jobs WHERE id=?", (job["id"],))["recovery_count"] == 2
