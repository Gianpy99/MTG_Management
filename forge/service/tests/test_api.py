import time

from fastapi.testclient import TestClient

from conftest import make_constructed


def wait_done(client, job_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/simulations/{job_id}").json()
        if job["status"] not in ("QUEUED", "RUNNING"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"{job_id} still {job['status']}")


def client():
    from forge_engine.api import app
    return TestClient(app)


def test_simulation_end_to_end(forge_home):
    with client() as c:
        assert c.get("/health").status_code == 200
        status = c.get("/api/forge/status").json()
        assert status["java_version"] == "17.0.99" and status["forge_version"] == "9.9.9"
        assert {d["name"] for d in c.get("/api/forge/test-decks").json()} == {"Deck_A", "Deck_B"}

        r = c.post("/api/simulations", json={
            "deck_a": "Deck_A",
            "deck_b": {"name": "Inline", "dck": make_constructed("Inline"), "ref": "my-slug"},
            "games": 4,
        })
        assert r.status_code == 200, r.text
        job = wait_done(c, r.json()["job_id"])
        assert job["status"] == "COMPLETED"
        assert (job["games_completed"], job["deck_a_wins"], job["deck_b_wins"], job["draws"]) == (4, 2, 1, 1)
        assert {"unsupported_cards", "ai_fallback", "small_sample"} <= set(job["result"]["quality_flags"])
        assert job["decks"][1]["deck_ref"] == "my-slug" and len(job["decks"][1]["deck_hash"]) == 64
        assert "Game Result: Game 4" in c.get(f"/api/simulations/{job['id']}/log").text
        assert len(c.get(f"/api/simulations/{job['id']}/results").json()["games"]) == 4
        assert [j["id"] for j in c.get("/api/simulations?deck=my-slug").json()] == [job["id"]]
        m = c.get("/api/matchups").json()
        assert m and m[0]["games"] == 4


def test_audit_verdict(forge_home):
    with client() as c:
        r = c.post("/api/forge/audit", json={"deck": "Deck_A"})
        job = wait_done(c, r.json()["job_id"])
        audit = job["result"]["audit"]
        assert audit["structure"] == "PASS" and audit["forge_load"] == "PASS"
        assert audit["status"] == "FAIL" and audit["red_cards"] == ["Bogus Card"]
        assert audit["yellow_cards"] == ["Gollum's Bite"]
        assert all(i["side"] in ("a", None) for i in job["issues"])


def test_validation_and_cancel(forge_home, monkeypatch):
    with client() as c:
        bad = {"name": "Small", "dck": "[metadata]\nName=Small\n[Main]\n10 Mountain\n"}
        r = c.post("/api/simulations", json={"deck_a": bad, "deck_b": "Deck_B"})
        assert r.status_code == 422 and r.json()["issues"][0]["issue_type"] == "deck_size"
        assert c.post("/api/simulations", json={"deck_a": "../etc", "deck_b": "Deck_B"}).status_code == 422
        assert c.post("/api/simulations", json={"deck_a": "Deck_A", "deck_b": "Deck_B", "games": 10**6}).status_code == 422
        assert c.post("/api/simulations", json={"deck_a": "Deck_A", "deck_b": "Nope"}).status_code == 404

        monkeypatch.setenv("FAKE_FORGE_DELAY", "1")
        job_id = c.post("/api/simulations", json={"deck_a": "Deck_A", "deck_b": "Deck_B", "games": 50}).json()["job_id"]
        deadline = time.monotonic() + 10
        while c.get(f"/api/simulations/{job_id}").json()["status"] != "RUNNING" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert c.post(f"/api/simulations/{job_id}/cancel").json()["status"] == "cancelling"
        job = wait_done(c, job_id)
        assert job["status"] == "CANCELLED" and job["games_completed"] < 50
