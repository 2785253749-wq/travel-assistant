from uuid import uuid4


def test_editor_and_mine_routes_serve_dedicated_travel_note_pages(client):
    note_id = uuid4()
    routes = [
        ("/community/notes/new", 'id="community-editor-form"', "/static/community-editor.js"),
        (f"/community/notes/{note_id}/edit", 'id="community-editor-form"', "/static/community-editor.js"),
        ("/community/mine", 'id="community-mine-list"', "/static/community-mine.js"),
        (f"/community/notes/{note_id}", 'id="community-note-content"', "/static/community-note.js"),
        ("/community/creators/voyage-traveler", 'id="community-creator-profile"', "/static/community-creator.js"),
    ]

    for route, marker, script in routes:
        response = client.get(route)
        assert response.status_code == 200
        assert marker in response.text
        assert script in response.text
