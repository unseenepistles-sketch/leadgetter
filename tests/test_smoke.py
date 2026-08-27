"""Smoke tests — exercise the full workflow on sample data, no external services.

Run with:  pytest -q
"""
import os
import tempfile

# Use an isolated temp DB before importing the app.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SHEETS_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import ads, campaigns, creators, leads  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402

init_db()
client = TestClient(app)


def test_dashboard_loads():
    r = client.get("/")
    assert r.status_code == 200
    assert "LeadSystem" in r.text


def test_discovery_returns_sample_creators():
    found, source = creators.discover("golf swing tips", "Kenya", "instagram", 10)
    assert source == "sample"          # no APIFY_TOKEN in tests
    assert len(found) == 10
    # Ranked by followers descending.
    fols = [c["followers"] for c in found]
    assert fols == sorted(fols, reverse=True)
    # Never fabricates contact info for individuals.
    assert all(c["public_email"] == "" for c in found)


def test_discovery_persists_and_dedupes():
    db = SessionLocal()
    try:
        found, _ = creators.discover("yoga", "", "instagram", 5)
        a = creators.save_creators(db, found, "yoga")
        b = creators.save_creators(db, found, "yoga")  # same set again
        assert len(a) == 5 and len(b) == 5
        handles = {c.handle for c in a}
        assert len(handles) == 5  # unique
    finally:
        db.close()


def test_lead_capture_requires_consent():
    db = SessionLocal()
    try:
        lead, msg, created = leads.capture(db, email="a@b.com", consent=False)
        assert lead is None and created is False and "consent" in msg.lower()

        lead, msg, created = leads.capture(db, email="a@b.com", consent=True, ip="1.2.3.4")
        assert lead is not None and created is True
        assert lead.consent is True and lead.consent_ts is not None
        assert lead.unsubscribe_token

        # A repeat submission is not a new create (welcome fires only once).
        lead2, _, created2 = leads.capture(db, email="a@b.com", consent=True)
        assert lead2.id == lead.id and created2 is False

        # Unsubscribe works via token.
        assert leads.unsubscribe(db, lead.unsubscribe_token) is True
        db.refresh(lead)
        assert lead.status == "unsubscribed"
    finally:
        db.close()


def test_invalid_email_rejected():
    db = SessionLocal()
    try:
        lead, msg, created = leads.capture(db, email="not-an-email", consent=True)
        assert lead is None and created is False and "valid" in msg.lower()
    finally:
        db.close()


def test_welcome_email_delivers_lead_magnet():
    # Welcome copy includes the lead-magnet link, and send is a logged dry-run.
    subject, body = campaigns.welcome_content("free first chapter",
                                              "https://example.com/chapter.pdf")
    assert "free first chapter" in subject
    assert "https://example.com/chapter.pdf" in body

    db = SessionLocal()
    try:
        lead, _, created = leads.capture(db, email="welcome@example.com", consent=True)
        assert created is True
        result = campaigns.send_welcome(db, lead, "free first chapter",
                                        "https://example.com/chapter.pdf")
        assert result["ok"] is True  # dry-run success (no SMTP configured)
    finally:
        db.close()


def test_subscribe_endpoint_and_unsubscribe_page():
    r = client.post("/subscribe", data={"email": "flow@example.com", "consent": "on"})
    assert r.status_code == 200
    assert "set" in r.text.lower()


def test_ad_audience_export():
    db = SessionLocal()
    try:
        found, _ = creators.discover("chess", "", "youtube", 5)
        creators.save_creators(db, found, "chess")
        csv_text = ads.audience_csv(db, "chess")
        assert "platform,handle" in csv_text
        kws = ads.interest_keywords(db, "chess")
        assert isinstance(kws, list)
    finally:
        db.close()


def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "ok"
    assert "ai" in body and "provider" in body["ai"]
