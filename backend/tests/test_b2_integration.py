"""B2 from B1 to B5 with disposable storage and fake-generated teaching text."""

import psycopg
import pytest
from test_b1_integration import b1_api, completed

from upraise_ai.main import app

pytest_plugins = ["test_a1_integration", "test_a3_integration", "test_b1"]


@pytest.mark.parametrize("consent,decision", [(["telemetry"], "HIGH_COGNITIVE_LOAD"), ([], "PROCEED")])
def test_b2_real_audit_keeps_only_ids_and_b5_preserves_course(integration, lesson, consent, decision):
    target, _, row, body = completed(integration, lesson)
    prepared = lesson[1].prepared_chunks(target["job_id"])
    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json={**body, "consent_scope": consent})
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        result = client.get(f"/v1/jobs/{target['job_id']}/result").json()
        chunk = result["chunks"][0]
        score = {"lesson_id": body["lesson_id"], "chunk_id": chunk["chunk_id"], "cognitive_load_score": 0.7}
        path = f"/v1/sessions/{session_id}"
        response = client.post(path+"/chunk-end", json=score)
        assert response.status_code == 200 and response.json() == {"decision": decision}
        session = app.state.sessions.get(session_id, row["client_id"])
        assert session.cognitive_load_state == (0.7 if consent else None)
        assert client.post(path+"/end", json={"reason": "COMPLETED"}).status_code == 204
        assert session.cognitive_load_state is None
        assert client.post(path+"/chunk-end", json=score).status_code == 410
        assert client.get(f"/v1/jobs/{target['job_id']}/result").status_code == 200
        assert client.get(f"/v1/jobs/{target['job_id']}/audio/{chunk['chunk_id']}").status_code == 200
    assert lesson[1].prepared_chunks(target["job_id"]) == prepared
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        events = conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s AND event_type='chunk_ended'",
                              (target["job_id"],)).fetchall()
    assert [event[0] for event in events] == [{"session_id": session_id, "chunk_id": chunk["chunk_id"]}]
