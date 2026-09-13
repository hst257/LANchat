import importlib
import base64
import os
import sys
import pytest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_test_app(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{tmp_path / 'chat-test.db'}"
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]
    module = importlib.import_module("app.main")
    module.UPLOAD_DIR = tmp_path / "uploads"
    return module.app


def test_registration_connections_and_websocket_message(tmp_path):
    application = load_test_app(tmp_path)
    with TestClient(application) as alice, TestClient(application) as bob:
        alice_registration = alice.post(
            "/api/register",
            json={"name": "Alice", "username": "alice", "password": "password1"},
        )
        bob_registration = bob.post(
            "/api/register",
            json={"name": "Bob", "username": "bob", "password": "password2"},
        )
        assert alice_registration.status_code == 201
        assert bob_registration.status_code == 201
        alice_id = alice_registration.json()["user"]["public_id"]
        bob_id = bob_registration.json()["user"]["public_id"]

        found = alice.get("/api/users/search", params={"q": bob_id})
        assert found.json()["user"]["relationship"] == "none"
        found_by_username = alice.get("/api/users/search", params={"username": "bob"})
        assert found_by_username.json()["user"]["public_id"] == bob_id
        assert found_by_username.json()["user"]["matched_by"] == "username"
        assert alice.post("/api/requests", json={"public_id": bob_id}).status_code == 201

        incoming = bob.get("/api/requests/incoming").json()["requests"]
        assert incoming[0]["sender"]["public_id"] == alice_id
        accepted = bob.post(
            f"/api/requests/{incoming[0]['id']}/respond", json={"action": "accept"}
        )
        assert accepted.json()["status"] == "accepted"
        assert alice.get("/api/connections").json()["connections"][0]["public_id"] == bob_id

        with alice.websocket_connect("/ws") as alice_ws:
            assert alice_ws.receive_json()["type"] == "ready"
            with bob.websocket_connect("/ws") as bob_ws:
                assert bob_ws.receive_json()["type"] == "ready"
                presence = alice_ws.receive_json()
                assert presence == {"type": "presence", "public_id": bob_id, "online": True}

                alice_ws.send_json(
                    {"type": "typing", "recipient_id": bob_id, "is_typing": True}
                )
                assert bob_ws.receive_json() == {
                    "type": "typing",
                    "from": alice_id,
                    "is_typing": True,
                }

                alice_ws.send_json(
                    {"type": "message", "recipient_id": bob_id, "content": "Hello Bob"}
                )
                alice_event = alice_ws.receive_json()
                bob_event = bob_ws.receive_json()
                assert alice_event["type"] == "message"
                assert bob_event["message"]["content"] == "Hello Bob"

                pixel_png = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
                image_response = alice.post(
                    f"/api/chats/{bob_id}/images",
                    files={"image": ("pixel.png", pixel_png, "image/png")},
                    data={"caption": "A tiny pixel"},
                )
                assert image_response.status_code == 201
                image_message = image_response.json()["message"]
                assert image_message["kind"] == "image"
                assert image_message["image_url"].startswith("/api/media/")
                assert alice_ws.receive_json()["message"]["id"] == image_message["id"]
                assert bob_ws.receive_json()["message"]["id"] == image_message["id"]
                media_response = bob.get(image_message["image_url"])
                assert media_response.status_code == 200
                assert media_response.content == pixel_png

        history = bob.get(f"/api/chats/{alice_id}/messages").json()["messages"]
        assert [message["content"] for message in history] == ["Hello Bob", "A tiny pixel"]
        assert [message["kind"] for message in history] == ["text", "image"]
        deleted = alice.delete(f"/api/message-actions/private/{image_message['id']}")
        assert deleted.status_code == 200
        assert "image_url" not in deleted.json()["message"]
        assert bob.get(image_message["image_url"]).status_code == 404


def test_auth_and_validation(tmp_path):
    application = load_test_app(tmp_path)
    with TestClient(application) as client:
        assert client.get("/api/me").status_code == 401
        assert client.post(
            "/api/register",
            json={"name": "   ", "username": "validname", "password": "password1"},
        ).status_code == 422


@pytest.mark.parametrize("scope", ["private", "group"])
def test_message_actions_and_profile_photo(tmp_path, scope):
    application = load_test_app(tmp_path)
    module = sys.modules["app.main"]
    with TestClient(application) as alice, TestClient(application) as bob:
        for client, name in ((alice, "alice"), (bob, "bob")):
            assert client.post("/api/register", json={"name": name, "username": name, "password": "password1"}).status_code == 201
        with module.SessionLocal() as db:
            users = list(db.scalars(module.select(module.User).order_by(module.User.id)))
            owner, other = users
            if scope == "group":
                group = module.ChatGroup(public_id="GROUP-TEST", name="Test", created_by_id=owner.id)
                db.add(group); db.flush()
                db.add_all([module.GroupMember(group_id=group.id, user_id=u.id, role="owner" if u.id == owner.id else "member") for u in users])
                message = module.GroupMessage(group_id=group.id, sender_id=owner.id, content="Original")
            else:
                message = module.Message(sender_id=owner.id, receiver_id=other.id, content="Original")
            db.add(message); db.commit()
            message_id = message.id
        path = f"/api/message-actions/{scope}/{message_id}"
        assert bob.patch(path, json={"content": "Unauthorized"}).status_code == 404
        assert bob.delete(path).status_code == 404
        assert alice.patch(path, json={"content": "  "}).status_code == 422
        with bob.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "ready"
            updated = alice.patch(path, json={"content": "Updated"})
            assert updated.status_code == 200
            assert updated.json()["message"]["edited"] is True
            event = socket.receive_json()
            assert event["type"] == ("message_updated" if scope == "private" else "group_message_updated")
            assert event["message"]["content"] == "Updated"
            assert alice.delete(path).json()["message"]["kind"] == "deleted"
            assert socket.receive_json()["message"]["content"] == ""
        assert alice.patch(path, json={"content": "Restore"}).status_code == 409
        history_path = f"/api/chats/{owner.public_id}/messages" if scope == "private" else "/api/groups/GROUP-TEST/messages"
        if scope == "private":
            with module.SessionLocal() as db:
                db.add(module.Connection(user_low_id=owner.id, user_high_id=other.id)); db.commit()
        assert bob.get(history_path).json()["messages"][0]["kind"] == "deleted"
        pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        assert alice.post("/api/me/avatar", files={"image": ("bad.svg", b"<svg/>", "image/svg+xml")}).status_code == 415
        photo = alice.post("/api/me/avatar", files={"image": ("photo.png", pixel, "image/png")})
        assert photo.status_code == 200
        url = photo.json()["user"]["avatar_url"]
        assert bob.get(url).content == pixel
        assert alice.get("/api/me").json()["user"]["avatar_url"] == url
        assert client.post(
            "/api/register",
            json={"name": "User", "username": "bad name", "password": "password1"},
        ).status_code == 422


def test_group_creation_member_management_and_realtime_chat(tmp_path):
    application = load_test_app(tmp_path)
    with (
        TestClient(application) as alice,
        TestClient(application) as bob,
        TestClient(application) as charlie,
    ):
        registrations = []
        for client, name in ((alice, "Alice"), (bob, "Bob"), (charlie, "Charlie")):
            registrations.append(
                client.post(
                    "/api/register",
                    json={"name": name, "username": name.lower(), "password": "password1"},
                ).json()["user"]
            )
        alice_user, bob_user, charlie_user = registrations

        for receiver, receiver_user in ((bob, bob_user), (charlie, charlie_user)):
            assert alice.post(
                "/api/requests", json={"public_id": receiver_user["public_id"]}
            ).status_code == 201
            request_id = receiver.get("/api/requests/incoming").json()["requests"][0]["id"]
            assert receiver.post(
                f"/api/requests/{request_id}/respond", json={"action": "accept"}
            ).status_code == 200

        created = alice.post(
            "/api/groups",
            json={"name": "Project Team", "member_ids": [bob_user["public_id"]]},
        )
        assert created.status_code == 201
        group_id = created.json()["group"]["public_id"]
        assert bob.get("/api/groups").json()["groups"][0]["public_id"] == group_id

        assert bob.post(
            f"/api/groups/{group_id}/members",
            json={"member_ids": [charlie_user["public_id"]]},
        ).status_code == 403
        added = alice.post(
            f"/api/groups/{group_id}/members",
            json={"member_ids": [charlie_user["public_id"]]},
        )
        assert added.status_code == 200
        assert added.json()["group"]["member_count"] == 3
        assert charlie.get("/api/groups").json()["groups"][0]["public_id"] == group_id

        with alice.websocket_connect("/ws") as alice_ws:
            assert alice_ws.receive_json()["type"] == "ready"
            with bob.websocket_connect("/ws") as bob_ws:
                assert bob_ws.receive_json()["type"] == "ready"
                assert alice_ws.receive_json()["type"] == "presence"

                alice_ws.send_json(
                    {"type": "group_typing", "group_id": group_id, "is_typing": True}
                )
                typing_event = bob_ws.receive_json()
                assert typing_event["type"] == "group_typing"
                assert typing_event["from"] == alice_user["public_id"]

                alice_ws.send_json(
                    {"type": "group_message", "group_id": group_id, "content": "Hello team"}
                )
                assert alice_ws.receive_json()["message"]["content"] == "Hello team"
                assert bob_ws.receive_json()["message"]["sender_name"] == "Alice"

        history = charlie.get(f"/api/groups/{group_id}/messages").json()
        assert history["group"]["member_count"] == 3
        assert history["messages"][0]["content"] == "Hello team"


def test_profiles_and_disconnect_delete_only_private_history(tmp_path):
    application = load_test_app(tmp_path)
    module = sys.modules["app.main"]
    with TestClient(application) as alice, TestClient(application) as bob:
        alice_user = alice.post(
            "/api/register",
            json={"name": "Alice", "username": "alice", "password": "password1"},
        ).json()["user"]
        bob_user = bob.post(
            "/api/register",
            json={"name": "Bob", "username": "bob", "password": "password1"},
        ).json()["user"]

        private_filename = "private-image.png"
        private_path = module.UPLOAD_DIR / private_filename
        private_path.write_bytes(b"private")
        with module.SessionLocal() as db:
            alice_row = db.scalar(module.select(module.User).where(module.User.public_id == alice_user["public_id"]))
            bob_row = db.scalar(module.select(module.User).where(module.User.public_id == bob_user["public_id"]))
            db.add(module.Connection(user_low_id=alice_row.id, user_high_id=bob_row.id))
            group = module.ChatGroup(public_id="GROUP-PROFILE", name="Shared", created_by_id=alice_row.id)
            db.add(group)
            db.flush()
            db.add_all([
                module.GroupMember(group_id=group.id, user_id=alice_row.id, role="owner"),
                module.GroupMember(group_id=group.id, user_id=bob_row.id, role="member"),
                module.Message(sender_id=alice_row.id, receiver_id=bob_row.id, content="Private text"),
                module.Message(sender_id=bob_row.id, receiver_id=alice_row.id, content="Private image", kind="image", media_filename=private_filename, media_mime="image/png", original_filename="private.png"),
                module.GroupMessage(group_id=group.id, sender_id=bob_row.id, content="Group history stays"),
            ])
            db.commit()

        profile = alice.get(f"/api/profiles/{bob_user['public_id']}")
        assert profile.status_code == 200
        assert profile.json()["profile"]["relationship"] == "connected"
        assert profile.json()["profile"]["mutual_group_count"] == 1

        updated = alice.patch(
            "/api/me/profile",
            json={"name": "Alice Cooper", "status_text": "At my desk", "bio": "LAN enthusiast"},
        )
        assert updated.status_code == 200
        assert updated.json()["user"]["bio"] == "LAN enthusiast"
        assert alice.get(f"/api/profiles/{alice_user['public_id']}").json()["profile"]["status_text"] == "At my desk"

        disconnected = alice.delete(f"/api/connections/{bob_user['public_id']}")
        assert disconnected.status_code == 200
        assert not private_path.exists()
        assert alice.get(f"/api/profiles/{bob_user['public_id']}").json()["profile"]["relationship"] == "none"
        assert alice.get(f"/api/chats/{bob_user['public_id']}/messages").status_code == 404
        group_history = alice.get("/api/groups/GROUP-PROFILE/messages")
        assert group_history.status_code == 200
        assert group_history.json()["messages"][0]["content"] == "Group history stays"
        with module.SessionLocal() as db:
            assert list(db.scalars(module.select(module.Message))) == []
            assert len(list(db.scalars(module.select(module.GroupMessage)))) == 1


def test_replies_reactions_and_forwarding(tmp_path):
    application = load_test_app(tmp_path)
    module = sys.modules["app.main"]
    with TestClient(application) as alice, TestClient(application) as bob:
        alice_user = alice.post(
            "/api/register",
            json={"name": "Alice", "username": "alice", "password": "password1"},
        ).json()["user"]
        bob_user = bob.post(
            "/api/register",
            json={"name": "Bob", "username": "bob", "password": "password1"},
        ).json()["user"]
        with module.SessionLocal() as db:
            alice_row = db.scalar(module.select(module.User).where(module.User.public_id == alice_user["public_id"]))
            bob_row = db.scalar(module.select(module.User).where(module.User.public_id == bob_user["public_id"]))
            db.add(module.Connection(user_low_id=alice_row.id, user_high_id=bob_row.id))
            group = module.ChatGroup(public_id="GROUP-FEATURES", name="Feature Test", created_by_id=alice_row.id)
            db.add(group)
            db.flush()
            db.add_all([
                module.GroupMember(group_id=group.id, user_id=alice_row.id, role="owner"),
                module.GroupMember(group_id=group.id, user_id=bob_row.id, role="member"),
            ])
            original = module.Message(sender_id=alice_row.id, receiver_id=bob_row.id, content="Original message")
            db.add(original)
            db.commit()
            original_id = original.id

        with bob.websocket_connect("/ws") as bob_socket:
            assert bob_socket.receive_json()["type"] == "ready"
            bob_socket.send_json({
                "type": "message",
                "recipient_id": alice_user["public_id"],
                "content": "This is a reply",
                "reply_to_id": original_id,
            })
            reply_event = bob_socket.receive_json()
            assert reply_event["message"]["reply_to"]["id"] == original_id
            assert reply_event["message"]["reply_to"]["sender_name"] == "Alice"

        with alice.websocket_connect("/ws") as alice_socket:
            assert alice_socket.receive_json()["type"] == "ready"
            reaction = bob.post(
                f"/api/message-actions/private/{original_id}/reactions",
                json={"emoji": "❤️"},
            )
            assert reaction.status_code == 200
            assert reaction.json()["message"]["reactions"][0]["count"] == 1
            reaction_event = alice_socket.receive_json()
            assert reaction_event["type"] == "message_updated"
            assert reaction_event["message"]["reactions"][0]["user_ids"] == [bob_user["public_id"]]

            forwarded = bob.post(
                f"/api/message-actions/private/{original_id}/forward",
                json={"target_type": "group", "target_id": "GROUP-FEATURES"},
            )
            assert forwarded.status_code == 201
            assert forwarded.json()["message"]["forwarded"] is True
            assert alice_socket.receive_json()["type"] == "group_message"

        history = alice.get("/api/groups/GROUP-FEATURES/messages").json()["messages"]
        assert history[0]["content"] == "Original message"
        assert history[0]["forwarded"] is True
        pixel = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        source_image = alice.post(
            f"/api/chats/{bob_user['public_id']}/images",
            files={"image": ("pixel.png", pixel, "image/png")},
            data={"caption": "Forward this photo"},
        ).json()["message"]
        forwarded_image = bob.post(
            f"/api/message-actions/private/{source_image['id']}/forward",
            json={"target_type": "group", "target_id": "GROUP-FEATURES"},
        )
        assert forwarded_image.status_code == 201
        forwarded_image_data = forwarded_image.json()["message"]
        assert forwarded_image_data["kind"] == "image"
        assert forwarded_image_data["forwarded"] is True
        assert forwarded_image_data["image_url"] != source_image["image_url"]
        assert alice.get(forwarded_image_data["image_url"]).content == pixel
        assert bob.post(
            f"/api/message-actions/private/{original_id}/reactions",
            json={"emoji": "not-an-emoji"},
        ).status_code == 422


def test_private_read_receipts_are_idempotent_and_realtime(tmp_path):
    application = load_test_app(tmp_path)
    module = sys.modules["app.main"]
    with TestClient(application) as alice, TestClient(application) as bob:
        alice_user = alice.post(
            "/api/register",
            json={"name": "Alice", "username": "alice", "password": "password1"},
        ).json()["user"]
        bob_user = bob.post(
            "/api/register",
            json={"name": "Bob", "username": "bob", "password": "password1"},
        ).json()["user"]
        with module.SessionLocal() as db:
            alice_row = db.scalar(module.select(module.User).where(module.User.public_id == alice_user["public_id"]))
            bob_row = db.scalar(module.select(module.User).where(module.User.public_id == bob_user["public_id"]))
            db.add(module.Connection(user_low_id=alice_row.id, user_high_id=bob_row.id))
            unread = module.Message(sender_id=bob_row.id, receiver_id=alice_row.id, content="Did you see this?")
            db.add(unread)
            db.commit()
            unread_id = unread.id

        with bob.websocket_connect("/ws") as bob_socket:
            assert bob_socket.receive_json()["type"] == "ready"
            marked = alice.post(f"/api/chats/{bob_user['public_id']}/read")
            assert marked.status_code == 200
            assert marked.json()["read_message_ids"] == [unread_id]
            receipt = bob_socket.receive_json()
            assert receipt["type"] == "messages_read"
            assert receipt["reader_id"] == alice_user["public_id"]
            assert receipt["message_ids"] == [unread_id]

        assert alice.post(f"/api/chats/{bob_user['public_id']}/read").json()["read_message_ids"] == []
        history = bob.get(f"/api/chats/{alice_user['public_id']}/messages").json()["messages"]
        assert history[0]["read"] is True
        assert history[0]["read_at"] is not None


def test_lan_link_prefers_the_active_route(monkeypatch, capsys):
    network = importlib.import_module("app.network")

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def connect(self, _target):
            pass

        def getsockname(self):
            return ("192.168.43.27", 50000)

    monkeypatch.setattr(network.socket, "socket", lambda *_: Probe())
    monkeypatch.setattr(network.socket, "gethostname", lambda: "chat-laptop")
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda *_: [
            (network.socket.AF_INET, 0, 0, "", ("172.31.0.1", 0)),
            (network.socket.AF_INET, 0, 0, "", ("192.168.43.27", 0)),
            (network.socket.AF_INET, 0, 0, "", ("127.0.0.1", 0)),
        ],
    )

    assert network.lan_ipv4_addresses() == ["192.168.43.27", "172.31.0.1"]
    network.print_lan_banner(8080)
    output = capsys.readouterr().out
    assert "http://192.168.43.27:8080" in output
    assert "<-- copy this" in output


def test_passwords_are_argon2id_hashed(tmp_path):
    application = load_test_app(tmp_path)
    module = sys.modules["app.main"]
    with TestClient(application) as client:
        response = client.post(
            "/api/register",
            json={"name": "Alice", "username": "alice", "password": "password1"},
        )
        assert response.status_code == 201
        with module.SessionLocal() as db:
            user = db.scalar(module.select(module.User).where(module.User.username == "alice"))
            assert user.password_salt == "argon2id"
            assert user.password_hash.startswith("$argon2id$")
            assert "password1" not in user.password_hash


def test_mp4_upload_is_stored_and_served_as_video(tmp_path):
    application = load_test_app(tmp_path)
    module = sys.modules["app.main"]
    with TestClient(application) as alice, TestClient(application) as bob:
        alice_user = alice.post(
            "/api/register",
            json={"name": "Alice", "username": "alice", "password": "password1"},
        ).json()["user"]
        bob_user = bob.post(
            "/api/register",
            json={"name": "Bob", "username": "bob", "password": "password1"},
        ).json()["user"]
        with module.SessionLocal() as db:
            alice_row = db.scalar(module.select(module.User).where(module.User.public_id == alice_user["public_id"]))
            bob_row = db.scalar(module.select(module.User).where(module.User.public_id == bob_user["public_id"]))
            db.add(module.Connection(user_low_id=alice_row.id, user_high_id=bob_row.id))
            db.commit()

        tiny_mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        sent = alice.post(
            f"/api/chats/{bob_user['public_id']}/media",
            files={"image": ("clip.mp4", tiny_mp4, "video/mp4")},
            data={"caption": "Tiny clip"},
        )
        assert sent.status_code == 201
        message = sent.json()["message"]
        assert message["kind"] == "video"
        assert message["media_url"].startswith("/api/media/")
        assert "image_url" not in message
        media = bob.get(message["media_url"])
        assert media.status_code == 200
        assert media.headers["content-type"] == "video/mp4"
        assert media.content == tiny_mp4


def test_s3_storage_uses_role_client_and_server_side_encryption(tmp_path, monkeypatch):
    storage = importlib.import_module("app.storage")

    class FakeS3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(("put", kwargs))

        def copy_object(self, **kwargs):
            self.calls.append(("copy", kwargs))

        def delete_object(self, **kwargs):
            self.calls.append(("delete", kwargs))

    fake = FakeS3()
    monkeypatch.setattr(storage.settings, "MEDIA_BACKEND", "s3")
    monkeypatch.setattr(storage.settings, "S3_BUCKET", "private-chat-media")
    monkeypatch.setattr(storage.settings, "S3_KMS_KEY_ID", "")
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)

    key = storage.put_media(b"image", ".png", "image/png", "messages", tmp_path)
    copied = storage.copy_media(key, "image/png", "group-messages", tmp_path)
    storage.delete_media(copied, tmp_path)

    assert key.startswith("messages/")
    assert copied.startswith("group-messages/")
    assert fake.calls[0][1]["ServerSideEncryption"] == "AES256"
    assert fake.calls[1][1]["CopySource"] == {
        "Bucket": "private-chat-media",
        "Key": key,
    }
    assert fake.calls[2][1] == {"Bucket": "private-chat-media", "Key": copied}
