from fastapi.testclient import TestClient

from app.main import app


def test_admin_community_page_serves_the_task13_shell():
    response = TestClient(app).get("/admin/community")

    assert response.status_code == 200
    assert 'id="admin-community-page"' in response.text
    assert "/static/admin-community.js" in response.text
    assert "/static/admin-community.css" in response.text
