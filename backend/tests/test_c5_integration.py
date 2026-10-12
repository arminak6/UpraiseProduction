"""Version provenance survives later deployments and lesson cleanup."""

import psycopg
from test_a3_integration import api, process
from test_a7_integration import parent, payload, submit_revision
from test_b1_integration import b1_api, completed

from upraise_ai.services.versions import VersionCatalog
from upraise_ai.storage.audit import AuditRepository

pytest_plugins = ["test_a1_integration", "test_a3_integration", "test_b1"]


def versioned_audit(integration, lesson):
    settings = lesson[0]
    audit = AuditRepository(settings.audit_dsn.get_secret_value(), settings=settings)
    integration[4].audit = audit
    lesson[2].audit = lesson[3].audit = audit
    return audit, VersionCatalog(settings).snapshot().model_dump(mode="json")


def test_c5_integration_a1_a3_a7_versions_link_to_immutable_audit_snapshot(integration, lesson):
    audit, snapshot = versioned_audit(integration, lesson)
    parent_id, original, _ = parent(integration, lesson)
    refs = VersionCatalog(lesson[0]).references()
    old_prepared = lesson[1].prepared_chunks(parent_id)
    assert all(original.versions[key] == value for key, value in refs.items())
    assert all(all(chunk["versions"][key] == value for key, value in refs.items()) for chunk in old_prepared)
    row = lesson[1].get(parent_id)
    assert row["result"]["configuration_id"] == snapshot["configuration_id"]
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        ingestion = conn.execute("SELECT details FROM ingestion_audit WHERE document_id=%s AND event_type='INDEX_VERIFIED'",
                                 (row["metadata"]["document_ids"][0],)).fetchone()[0]
        creation = conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s AND event_type='DRAFT_CREATED'",
                                (parent_id,)).fetchone()[0]
    assert ingestion["configuration_id"] == snapshot["configuration_id"]
    assert ingestion["version_snapshot"] == creation["version_snapshot"] == snapshot
    with api(integration, lesson) as client:
        revised_id = submit_revision(client, payload(parent_id, original))
        revised = process(lesson)
        assert revised["status"] == "DONE", revised["error_code"]
        result = client.get(f"/v1/jobs/{revised_id}/result").json()
        assert result["versions"]["configuration_id"] == original.versions["configuration_id"]
        assert result["versions"]["revision_configuration_id"] == snapshot["configuration_id"]
        assert revised["result"]["configuration_id"] == snapshot["configuration_id"]
    assert lesson[1].prepared_chunks(parent_id) == old_prepared
    # A later process can read an old lesson without relabelling its generation.
    changed = lesson[0].model_copy(update={"lesson_generation_model_id": "invented-new-generation-model"})
    new_audit = AuditRepository(changed.audit_dsn.get_secret_value(), settings=changed)
    new_audit.lesson_access(row, "VERSION_TEST_READ", {"result_sha256": row["result"]["result_sha256"]})
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        events = dict(conn.execute("SELECT event_type,details FROM lesson_audit WHERE job_id=%s "
                                  "AND event_type IN ('DRAFT_CREATED','VERSION_TEST_READ')", (parent_id,)).fetchall())
    assert events["DRAFT_CREATED"]["version_snapshot"] == snapshot
    assert events["VERSION_TEST_READ"]["version_snapshot"]["configuration_id"] != snapshot["configuration_id"]
    assert lesson[1].prepared_chunks(parent_id) == old_prepared


def test_c5_integration_b2_audit_tracks_policy_without_measurement(integration, lesson):
    _, snapshot = versioned_audit(integration, lesson)
    target, _, row, body = completed(integration, lesson)
    chunk = str(lesson[1].prepared_chunks(target["job_id"])[0]["chunk_id"])
    with b1_api(integration, lesson) as client:
        started = client.post("/v1/sessions", json={**body, "consent_scope": ["telemetry"]})
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        path = f"/v1/sessions/{session_id}"
        response = client.post(path + "/chunk-end", json={"lesson_id": body["lesson_id"],
                               "chunk_id": chunk, "cognitive_load_score": 0.7})
        assert response.status_code == 200 and response.json() == {"decision": "HIGH_COGNITIVE_LOAD"}
        assert client.post(path + "/end", json={"reason": "COMPLETED"}).status_code == 204
    with psycopg.connect(lesson[0].audit_dsn.get_secret_value()) as conn:
        event = conn.execute("SELECT details FROM lesson_audit WHERE job_id=%s AND event_type='chunk_ended'",
                             (row["job_id"],)).fetchone()[0]
    assert event == {"session_id": session_id, "chunk_id": str(chunk), "version_snapshot": snapshot}
