import json
import os
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, inspect, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    COOKIE_NAME,
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    password_needs_rehash,
    user_from_token,
    verify_password,
)
from .database import Base, SessionLocal, engine, get_db
from .models import (
    ChatGroup,
    Connection,
    ConnectionRequest,
    GroupMember,
    GroupMessage,
    GroupMessageReaction,
    Message,
    MessageReaction,
    User,
)
from .network import print_lan_banner
from . import settings
from .security import SecurityMiddleware, websocket_origin_allowed
from .storage import delete_media, delivery_url, put_media
from .schemas import (
    ConnectionRequestInput,
    GroupCreateInput,
    GroupMembersInput,
    LoginInput,
    RegisterInput,
    RequestResponseInput,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
FRONTEND_DIR = BASE_DIR / "frontend_dist"
UPLOAD_DIR = BASE_DIR.parent / "uploads"
MAX_IMAGE_BYTES = settings.MAX_IMAGE_BYTES
MAX_VIDEO_BYTES = settings.MAX_VIDEO_BYTES


def upgrade_message_table() -> None:
    """Keep existing prototype databases compatible without requiring Alembic."""
    existing = {column["name"] for column in inspect(engine).get_columns("messages")}
    statements = {
        "kind": "ALTER TABLE messages ADD COLUMN kind VARCHAR(10) NOT NULL DEFAULT 'text'",
        "media_filename": "ALTER TABLE messages ADD COLUMN media_filename VARCHAR(120)",
        "media_mime": "ALTER TABLE messages ADD COLUMN media_mime VARCHAR(50)",
        "original_filename": "ALTER TABLE messages ADD COLUMN original_filename VARCHAR(255)",
    }
    with engine.begin() as connection:
        for column, statement in statements.items():
            if column not in existing:
                connection.execute(text(statement))


def image_format(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def media_format(data: bytes) -> tuple[str, str] | None:
    image = image_format(data)
    if image:
        return image
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4", ".mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm", ".webm"
    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_production_settings()
    Base.metadata.create_all(bind=engine)
    upgrade_message_table()
    with engine.begin() as connection:
        for table, additions in {
            "users": {
                "avatar_filename": "VARCHAR(120)",
                "avatar_mime": "VARCHAR(50)",
                "bio": "TEXT NOT NULL DEFAULT ''",
                "status_text": "VARCHAR(80) NOT NULL DEFAULT 'Available'",
            },
            "messages": {
                "edited_at": "TIMESTAMP",
                "reply_to_id": "INTEGER",
                "forwarded": "BOOLEAN NOT NULL DEFAULT false",
                "read_at": "TIMESTAMP",
            },
            "group_messages": {
                "edited_at": "TIMESTAMP",
                "reply_to_id": "INTEGER",
                "forwarded": "BOOLEAN NOT NULL DEFAULT false",
            },
        }.items():
            columns = {column["name"] for column in inspect(connection).get_columns(table)}
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not settings.IS_PRODUCTION:
        try:
            display_port = int(os.getenv("LAN_CHAT_PORT", "8000"))
        except ValueError:
            display_port = 8000
        print_lan_banner(display_port)
    yield


app = FastAPI(
    title="LAN Chat",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.IS_PRODUCTION else "/docs",
    redoc_url=None if settings.IS_PRODUCTION else "/redoc",
    openapi_url=None if settings.IS_PRODUCTION else "/openapi.json",
)
app.add_middleware(SecurityMiddleware)


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self.active.get(user_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self.active.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        return bool(self.active.get(user_id))

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        stale: list[WebSocket] = []
        for socket in list(self.active.get(user_id, set())):
            try:
                await socket.send_json(payload)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(user_id, socket)


manager = ConnectionManager()


def user_json(user: User) -> dict:
    return {
        "public_id": user.public_id,
        "name": user.name,
        "username": user.username,
        "avatar_url": f"/api/avatars/{user.public_id}?v={user.avatar_filename}" if user.avatar_filename else None,
        "bio": user.bio,
        "status_text": user.status_text,
    }


def reaction_json(db: Session, model, message_id: int) -> list[dict]:
    rows = db.execute(
        select(model.emoji, User.public_id, User.name)
        .join(User, User.id == model.user_id)
        .where(model.message_id == message_id)
        .order_by(model.id)
    ).all()
    grouped: dict[str, dict] = {}
    for emoji, public_id, name in rows:
        item = grouped.setdefault(
            emoji, {"emoji": emoji, "count": 0, "user_ids": [], "names": []}
        )
        item["count"] += 1
        item["user_ids"].append(public_id)
        item["names"].append(name)
    return list(grouped.values())


def reply_json(db: Session, model, reply_to_id: int | None) -> dict | None:
    if not reply_to_id:
        return None
    reply = db.get(model, reply_to_id)
    if not reply:
        return None
    sender = db.get(User, reply.sender_id)
    return {
        "id": reply.id,
        "sender_id": sender.public_id,
        "sender_name": sender.name,
        "content": reply.content,
        "kind": reply.kind,
    }


def message_json(
    message: Message,
    sender_public_id: str,
    receiver_public_id: str,
    db: Session | None = None,
) -> dict:
    payload = {
        "id": message.id,
        "sender_id": sender_public_id,
        "receiver_id": receiver_public_id,
        "content": message.content,
        "edited": message.edited_at is not None,
        "kind": message.kind,
        "forwarded": message.forwarded,
        "read": message.read_at is not None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "created_at": message.created_at.isoformat(),
    }
    if db:
        payload["reply_to"] = reply_json(db, Message, message.reply_to_id)
        payload["reactions"] = reaction_json(db, MessageReaction, message.id)
    if message.kind in {"image", "video"}:
        payload["media_url"] = f"/api/media/{message.id}"
        if message.kind == "image":
            payload["image_url"] = payload["media_url"]
        payload["original_filename"] = message.original_filename
    return payload


def group_message_json(
    message: GroupMessage, sender: User, db: Session | None = None
) -> dict:
    payload = {
        "id": message.id,
        "group_id": message.group_id,
        "sender_id": sender.public_id,
        "sender_name": sender.name,
        "content": message.content,
        "edited": message.edited_at is not None,
        "kind": message.kind,
        "forwarded": message.forwarded,
        "created_at": message.created_at.isoformat(),
    }
    if db:
        payload["reply_to"] = reply_json(db, GroupMessage, message.reply_to_id)
        payload["reactions"] = reaction_json(db, GroupMessageReaction, message.id)
    if message.kind in {"image", "video"}:
        payload["media_url"] = f"/api/group-media/{message.id}"
        if message.kind == "image":
            payload["image_url"] = payload["media_url"]
        payload["original_filename"] = message.original_filename
    return payload


def connection_between(db: Session, first_id: int, second_id: int) -> Connection | None:
    low, high = sorted((first_id, second_id))
    return db.scalar(
        select(Connection).where(
            Connection.user_low_id == low, Connection.user_high_id == high
        )
    )


def connected_user_ids(db: Session, user_id: int) -> list[int]:
    rows = db.execute(
        select(Connection.user_low_id, Connection.user_high_id).where(
            or_(Connection.user_low_id == user_id, Connection.user_high_id == user_id)
        )
    ).all()
    return [high if low == user_id else low for low, high in rows]


def group_membership(db: Session, group_id: int, user_id: int) -> GroupMember | None:
    return db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )


def group_member_ids(db: Session, group_id: int) -> list[int]:
    return list(
        db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group_id)).all()
    )


def private_reply_target(
    db: Session, reply_to_id: int | None, first_id: int, second_id: int
) -> Message | None:
    if not reply_to_id:
        return None
    reply = db.get(Message, reply_to_id)
    if not reply or reply.kind == "deleted":
        return None
    participants = {reply.sender_id, reply.receiver_id}
    return reply if participants == {first_id, second_id} else None


def group_reply_target(
    db: Session, reply_to_id: int | None, group_id: int
) -> GroupMessage | None:
    if not reply_to_id:
        return None
    reply = db.get(GroupMessage, reply_to_id)
    return reply if reply and reply.group_id == group_id and reply.kind != "deleted" else None


def group_json(db: Session, group: ChatGroup, current_user_id: int) -> dict:
    rows = db.execute(
        select(GroupMember, User)
        .join(User, User.id == GroupMember.user_id)
        .where(GroupMember.group_id == group.id)
        .order_by(User.name)
    ).all()
    members = [
        {
            **user_json(member_user),
            "role": membership.role,
            "online": manager.is_online(member_user.id),
        }
        for membership, member_user in rows
    ]
    current_membership = next(
        membership for membership, member_user in rows if member_user.id == current_user_id
    )
    return {
        "public_id": group.public_id,
        "name": group.name,
        "role": current_membership.role,
        "member_count": len(members),
        "online_count": sum(member["online"] for member in members),
        "members": members,
        "created_at": group.created_at.isoformat(),
    }


async def notify_presence(user_id: int) -> None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user:
            return
        payload = {
            "type": "presence",
            "public_id": user.public_id,
            "online": manager.is_online(user_id),
        }
        notify_ids = set(connected_user_ids(db, user_id))
        group_ids = list(
            db.scalars(
                select(GroupMember.group_id).where(GroupMember.user_id == user_id)
            ).all()
        )
        if group_ids:
            notify_ids.update(
                db.scalars(
                    select(GroupMember.user_id).where(
                        GroupMember.group_id.in_(group_ids),
                        GroupMember.user_id != user_id,
                    )
                ).all()
            )
    for notify_id in notify_ids:
        await manager.send_to_user(notify_id, payload)


def frontend_index() -> FileResponse:
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/login")


@app.get("/healthz", include_in_schema=False)
def healthcheck() -> dict:
    return {"status": "ok"}


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return frontend_index()


@app.get("/connections", include_in_schema=False)
def connections_page() -> FileResponse:
    return frontend_index()


@app.get("/chat", include_in_schema=False)
def chat_page() -> FileResponse:
    return frontend_index()


@app.get("/chat/{contact_public_id}", include_in_schema=False)
def contact_chat_page(contact_public_id: str) -> FileResponse:
    return frontend_index()


@app.get("/group/{group_public_id}", include_in_schema=False)
def group_chat_page(group_public_id: str) -> FileResponse:
    return frontend_index()


@app.get("/profile/{public_id}", include_in_schema=False)
def profile_page(public_id: str) -> FileResponse:
    return frontend_index()


@app.post("/api/register", status_code=201)
def register(payload: RegisterInput, response: Response, db: Session = Depends(get_db)) -> dict:
    username = payload.username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=409, detail="That username is already taken")

    salt, password_hash = hash_password(payload.password)
    for _ in range(5):
        public_id = f"CHAT-{secrets.token_hex(4).upper()}"
        if not db.scalar(select(User.id).where(User.public_id == public_id)):
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate a user ID")

    user = User(
        public_id=public_id,
        name=payload.name.strip(),
        username=username,
        password_salt=salt,
        password_hash=password_hash,
    )
    db.add(user)
    try:
        db.flush()
        create_session(db, user.id, response)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That username is already taken")
    return {"user": user_json(user)}


@app.post("/api/login")
def login(payload: LoginInput, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.username == payload.username.strip().lower()))
    if not user or not verify_password(payload.password, user.password_salt, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if password_needs_rehash(user.password_hash):
        user.password_salt, user.password_hash = hash_password(payload.password)
    create_session(db, user.id, response)
    return {"user": user_json(user)}


@app.post("/api/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    delete_session(db, request.cookies.get(COOKIE_NAME), response)
    response.status_code = 204
    return response


@app.get("/api/me")
def me(current_user: User = Depends(get_current_user)) -> dict:
    return {"user": user_json(current_user)}


@app.get("/api/connections")
def list_connections(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    contact_ids = connected_user_ids(db, current_user.id)
    contacts = db.scalars(select(User).where(User.id.in_(contact_ids)).order_by(User.name)).all() if contact_ids else []
    return {
        "connections": [
            {**user_json(contact), "online": manager.is_online(contact.id)}
            for contact in contacts
        ]
    }


@app.get("/api/users/search")
def search_user(
    q: str | None = Query(default=None, min_length=3, max_length=20),
    username: str | None = Query(default=None, min_length=3, max_length=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if bool(q) == bool(username):
        raise HTTPException(
            status_code=400,
            detail="Search using either a public ID or a username",
        )
    if username:
        candidate = db.scalar(
            select(User).where(User.username == username.strip().lower())
        )
        matched_by = "username"
    else:
        candidate = db.scalar(
            select(User).where(User.public_id == q.strip().upper())
        )
        matched_by = "public_id"
    if not candidate or candidate.id == current_user.id:
        return {"user": None}

    relationship = "none"
    if connection_between(db, current_user.id, candidate.id):
        relationship = "connected"
    else:
        pending = db.scalar(
            select(ConnectionRequest).where(
                ConnectionRequest.status == "pending",
                or_(
                    and_(
                        ConnectionRequest.sender_id == current_user.id,
                        ConnectionRequest.receiver_id == candidate.id,
                    ),
                    and_(
                        ConnectionRequest.sender_id == candidate.id,
                        ConnectionRequest.receiver_id == current_user.id,
                    ),
                ),
            )
        )
        if pending:
            relationship = "sent" if pending.sender_id == current_user.id else "incoming"
    return {
        "user": {
            **user_json(candidate),
            "relationship": relationship,
            "matched_by": matched_by,
        }
    }


@app.post("/api/requests", status_code=201)
async def send_connection_request(
    payload: ConnectionRequestInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    receiver = db.scalar(
        select(User).where(User.public_id == payload.public_id.strip().upper())
    )
    if not receiver:
        raise HTTPException(status_code=404, detail="No user has that ID")
    if receiver.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot connect with yourself")
    if connection_between(db, current_user.id, receiver.id):
        raise HTTPException(status_code=409, detail="You are already connected")

    reverse = db.scalar(
        select(ConnectionRequest).where(
            ConnectionRequest.sender_id == receiver.id,
            ConnectionRequest.receiver_id == current_user.id,
            ConnectionRequest.status == "pending",
        )
    )
    if reverse:
        raise HTTPException(status_code=409, detail="This user already sent you a request")

    existing = db.scalar(
        select(ConnectionRequest).where(
            ConnectionRequest.sender_id == current_user.id,
            ConnectionRequest.receiver_id == receiver.id,
        )
    )
    if existing and existing.status == "pending":
        raise HTTPException(status_code=409, detail="Request already sent")
    if existing:
        existing.status = "pending"
        existing.created_at = datetime.now(timezone.utc)
        existing.responded_at = None
        request_row = existing
    else:
        request_row = ConnectionRequest(sender_id=current_user.id, receiver_id=receiver.id)
        db.add(request_row)
    db.commit()
    db.refresh(request_row)
    await manager.send_to_user(
        receiver.id,
        {"type": "connection_request", "request": {"id": request_row.id, "sender": user_json(current_user)}},
    )
    return {"request_id": request_row.id}


@app.get("/api/requests/incoming")
def incoming_requests(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    rows = db.execute(
        select(ConnectionRequest, User)
        .join(User, User.id == ConnectionRequest.sender_id)
        .where(
            ConnectionRequest.receiver_id == current_user.id,
            ConnectionRequest.status == "pending",
        )
        .order_by(ConnectionRequest.created_at.desc())
    ).all()
    return {
        "requests": [
            {
                "id": request_row.id,
                "sender": user_json(sender),
                "created_at": request_row.created_at.isoformat(),
            }
            for request_row, sender in rows
        ]
    }


@app.post("/api/requests/{request_id}/respond")
async def respond_to_request(
    request_id: int,
    payload: RequestResponseInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    request_row = db.scalar(
        select(ConnectionRequest).where(
            ConnectionRequest.id == request_id,
            ConnectionRequest.receiver_id == current_user.id,
        )
    )
    if not request_row:
        raise HTTPException(status_code=404, detail="Request not found")
    if request_row.status != "pending":
        raise HTTPException(status_code=409, detail="Request was already answered")

    request_row.status = "accepted" if payload.action == "accept" else "rejected"
    request_row.responded_at = datetime.now(timezone.utc)
    if payload.action == "accept":
        low, high = sorted((request_row.sender_id, request_row.receiver_id))
        if not connection_between(db, low, high):
            db.add(Connection(user_low_id=low, user_high_id=high))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Connection already exists")

    event = {"type": "request_answered", "action": payload.action, "by": user_json(current_user)}
    await manager.send_to_user(request_row.sender_id, event)
    if payload.action == "accept":
        await manager.send_to_user(current_user.id, {"type": "connections_changed"})
        await manager.send_to_user(request_row.sender_id, {"type": "connections_changed"})
    return {"status": request_row.status}


@app.get("/api/groups")
def list_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    groups = list(
        db.scalars(
            select(ChatGroup)
            .join(GroupMember, GroupMember.group_id == ChatGroup.id)
            .where(GroupMember.user_id == current_user.id)
            .order_by(ChatGroup.name)
        ).all()
    )
    return {"groups": [group_json(db, group, current_user.id) for group in groups]}


@app.post("/api/groups", status_code=201)
async def create_group(
    payload: GroupCreateInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    selected_users = list(
        db.scalars(select(User).where(User.public_id.in_(payload.member_ids))).all()
    )
    if len(selected_users) != len(payload.member_ids):
        raise HTTPException(status_code=404, detail="One or more selected users were not found")
    if any(
        user.id == current_user.id
        or not connection_between(db, current_user.id, user.id)
        for user in selected_users
    ):
        raise HTTPException(
            status_code=400,
            detail="Group members must be your accepted connections",
        )

    for _ in range(5):
        public_id = f"GROUP-{secrets.token_hex(4).upper()}"
        if not db.scalar(select(ChatGroup.id).where(ChatGroup.public_id == public_id)):
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate a group ID")

    group = ChatGroup(
        public_id=public_id,
        name=payload.name,
        created_by_id=current_user.id,
    )
    db.add(group)
    db.flush()
    db.add(GroupMember(group_id=group.id, user_id=current_user.id, role="owner"))
    for member_user in selected_users:
        db.add(GroupMember(group_id=group.id, user_id=member_user.id, role="member"))
    db.commit()
    db.refresh(group)

    result = group_json(db, group, current_user.id)
    for member_user in selected_users:
        await manager.send_to_user(member_user.id, {"type": "groups_changed"})
    await manager.send_to_user(current_user.id, {"type": "groups_changed"})
    return {"group": result}


@app.get("/api/groups/{group_public_id}/messages")
def get_group_messages(
    group_public_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    group = db.scalar(
        select(ChatGroup).where(ChatGroup.public_id == group_public_id.upper())
    )
    if not group or not group_membership(db, group.id, current_user.id):
        raise HTTPException(status_code=404, detail="Group not found")

    query = (
        select(GroupMessage, User)
        .join(User, User.id == GroupMessage.sender_id)
        .where(GroupMessage.group_id == group.id)
    )
    if before_id:
        query = query.where(GroupMessage.id < before_id)
    rows = list(db.execute(query.order_by(GroupMessage.id.desc()).limit(limit)).all())
    rows.reverse()
    return {
        "group": group_json(db, group, current_user.id),
        "messages": [group_message_json(message, sender, db) for message, sender in rows],
    }


@app.post("/api/groups/{group_public_id}/members")
async def add_group_members(
    group_public_id: str,
    payload: GroupMembersInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    group = db.scalar(
        select(ChatGroup).where(ChatGroup.public_id == group_public_id.upper())
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    membership = group_membership(db, group.id, current_user.id)
    if not membership or membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the group owner can add members")

    selected_users = list(
        db.scalars(select(User).where(User.public_id.in_(payload.member_ids))).all()
    )
    if len(selected_users) != len(payload.member_ids):
        raise HTTPException(status_code=404, detail="One or more selected users were not found")
    existing_ids = set(group_member_ids(db, group.id))
    for member_user in selected_users:
        if member_user.id in existing_ids:
            raise HTTPException(status_code=409, detail=f"{member_user.name} is already a member")
        if not connection_between(db, current_user.id, member_user.id):
            raise HTTPException(
                status_code=400,
                detail="New members must be your accepted connections",
            )
        db.add(GroupMember(group_id=group.id, user_id=member_user.id, role="member"))
    db.commit()

    all_member_ids = group_member_ids(db, group.id)
    for member_id in all_member_ids:
        await manager.send_to_user(
            member_id,
            {"type": "group_members_changed", "group_id": group.public_id},
        )
        if member_id in {user.id for user in selected_users}:
            await manager.send_to_user(member_id, {"type": "groups_changed"})
    return {"group": group_json(db, group, current_user.id)}


@app.get("/api/chats/{contact_public_id}/messages")
def get_messages(
    contact_public_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    contact = db.scalar(select(User).where(User.public_id == contact_public_id.upper()))
    if not contact or not connection_between(db, current_user.id, contact.id):
        raise HTTPException(status_code=404, detail="Connection not found")

    conversation = or_(
        and_(Message.sender_id == current_user.id, Message.receiver_id == contact.id),
        and_(Message.sender_id == contact.id, Message.receiver_id == current_user.id),
    )
    query = select(Message).where(conversation)
    if before_id:
        query = query.where(Message.id < before_id)
    messages = list(db.scalars(query.order_by(Message.id.desc()).limit(limit)).all())
    messages.reverse()

    def serialize(row: Message) -> dict:
        sender_public_id = current_user.public_id if row.sender_id == current_user.id else contact.public_id
        receiver_public_id = contact.public_id if row.receiver_id == contact.id else current_user.public_id
        return message_json(row, sender_public_id, receiver_public_id, db)

    return {
        "contact": {**user_json(contact), "online": manager.is_online(contact.id)},
        "messages": [serialize(row) for row in messages],
    }


@app.post("/api/chats/{contact_public_id}/read")
async def mark_chat_read(
    contact_public_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    contact = db.scalar(select(User).where(User.public_id == contact_public_id.upper()))
    if not contact or not connection_between(db, current_user.id, contact.id):
        raise HTTPException(status_code=404, detail="Connection not found")
    unread = list(
        db.scalars(
            select(Message).where(
                Message.sender_id == contact.id,
                Message.receiver_id == current_user.id,
                Message.read_at.is_(None),
                Message.kind != "deleted",
            )
        ).all()
    )
    if not unread:
        return {"read_message_ids": []}
    read_at = datetime.now(timezone.utc)
    for message in unread:
        message.read_at = read_at
    db.commit()
    message_ids = [message.id for message in unread]
    await manager.send_to_user(
        contact.id,
        {
            "type": "messages_read",
            "reader_id": current_user.public_id,
            "message_ids": message_ids,
            "read_at": read_at.isoformat(),
        },
    )
    return {"read_message_ids": message_ids, "read_at": read_at.isoformat()}


@app.post("/api/chats/{contact_public_id}/media", status_code=201)
@app.post("/api/chats/{contact_public_id}/images", status_code=201, include_in_schema=False)
async def send_image(
    contact_public_id: str,
    image: UploadFile = File(...),
    caption: str = Form(default="", max_length=1000),
    reply_to_id: int | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    contact = db.scalar(select(User).where(User.public_id == contact_public_id.upper()))
    if not contact or not connection_between(db, current_user.id, contact.id):
        raise HTTPException(status_code=404, detail="Connection not found")
    if reply_to_id and not private_reply_target(
        db, reply_to_id, current_user.id, contact.id
    ):
        raise HTTPException(status_code=400, detail="Reply message is not in this chat")

    data = await image.read(MAX_VIDEO_BYTES + 1)
    detected = media_format(data)
    if not detected:
        raise HTTPException(
            status_code=415,
            detail="Use a PNG, JPEG, GIF, WebP, MP4, or WebM file",
        )
    media_mime, extension = detected
    size_limit = MAX_IMAGE_BYTES if media_mime.startswith("image/") else MAX_VIDEO_BYTES
    if len(data) > size_limit:
        label = "Images" if media_mime.startswith("image/") else "Videos"
        raise HTTPException(
            status_code=413,
            detail=f"{label} must be {size_limit // 1024 // 1024} MB or smaller",
        )
    original_name = (image.filename or f"image{extension}").replace("\\", "/").split("/")[-1]
    stored_name = put_media(data, extension, media_mime, "messages", UPLOAD_DIR)

    message = Message(
        sender_id=current_user.id,
        receiver_id=contact.id,
        content=caption.strip(),
        kind="image" if media_mime.startswith("image/") else "video",
        media_filename=stored_name,
        media_mime=media_mime,
        original_filename=original_name[:255],
        reply_to_id=reply_to_id,
    )
    db.add(message)
    try:
        db.commit()
        db.refresh(message)
    except Exception:
        db.rollback()
        delete_media(stored_name, UPLOAD_DIR)
        raise

    payload = message_json(message, current_user.public_id, contact.public_id, db)
    event = {"type": "message", "message": payload}
    await manager.send_to_user(current_user.id, event)
    await manager.send_to_user(contact.id, event)
    return {"message": payload}


@app.get("/api/media/{message_id}")
def get_message_media(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = db.get(Message, message_id)
    if (
        not message
        or message.kind not in {"image", "video"}
        or current_user.id not in (message.sender_id, message.receiver_id)
        or not message.media_filename
        or not message.media_mime
    ):
        raise HTTPException(status_code=404, detail="Media not found")
    if settings.MEDIA_BACKEND == "s3":
        return RedirectResponse(delivery_url(message.media_filename), status_code=307)
    path = UPLOAD_DIR / message.media_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(
        path,
        media_type=message.media_mime,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/groups/{group_public_id}/media", status_code=201)
@app.post("/api/groups/{group_public_id}/images", status_code=201, include_in_schema=False)
async def send_group_image(
    group_public_id: str,
    image: UploadFile = File(...),
    caption: str = Form(default="", max_length=1000),
    reply_to_id: int | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    group = db.scalar(
        select(ChatGroup).where(ChatGroup.public_id == group_public_id.upper())
    )
    if not group or not group_membership(db, group.id, current_user.id):
        raise HTTPException(status_code=404, detail="Group not found")
    if reply_to_id and not group_reply_target(db, reply_to_id, group.id):
        raise HTTPException(status_code=400, detail="Reply message is not in this group")

    data = await image.read(MAX_VIDEO_BYTES + 1)
    detected = media_format(data)
    if not detected:
        raise HTTPException(
            status_code=415,
            detail="Use a PNG, JPEG, GIF, WebP, MP4, or WebM file",
        )
    media_mime, extension = detected
    size_limit = MAX_IMAGE_BYTES if media_mime.startswith("image/") else MAX_VIDEO_BYTES
    if len(data) > size_limit:
        label = "Images" if media_mime.startswith("image/") else "Videos"
        raise HTTPException(
            status_code=413,
            detail=f"{label} must be {size_limit // 1024 // 1024} MB or smaller",
        )
    original_name = (image.filename or f"image{extension}").replace("\\", "/").split("/")[-1]
    stored_name = put_media(data, extension, media_mime, "group-messages", UPLOAD_DIR)

    message = GroupMessage(
        group_id=group.id,
        sender_id=current_user.id,
        content=caption.strip(),
        kind="image" if media_mime.startswith("image/") else "video",
        media_filename=stored_name,
        media_mime=media_mime,
        original_filename=original_name[:255],
        reply_to_id=reply_to_id,
    )
    db.add(message)
    try:
        db.commit()
        db.refresh(message)
    except Exception:
        db.rollback()
        delete_media(stored_name, UPLOAD_DIR)
        raise

    payload = group_message_json(message, current_user, db)
    event = {"type": "group_message", "group_id": group.public_id, "message": payload}
    for member_id in group_member_ids(db, group.id):
        await manager.send_to_user(member_id, event)
    return {"message": payload}


@app.get("/api/group-media/{message_id}")
def get_group_message_media(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = db.get(GroupMessage, message_id)
    if (
        not message
        or message.kind not in {"image", "video"}
        or not group_membership(db, message.group_id, current_user.id)
        or not message.media_filename
        or not message.media_mime
    ):
        raise HTTPException(status_code=404, detail="Media not found")
    if settings.MEDIA_BACKEND == "s3":
        return RedirectResponse(delivery_url(message.media_filename), status_code=307)
    path = UPLOAD_DIR / message.media_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(
        path,
        media_type=message.media_mime,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if not websocket_origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008, reason="Origin not allowed")
        return
    with SessionLocal() as db:
        current_user = user_from_token(db, websocket.cookies.get(COOKIE_NAME))
        if not current_user:
            await websocket.close(code=4401)
            return
        user_id = current_user.id
        public_id = current_user.public_id

    was_online = manager.is_online(user_id)
    await manager.connect(user_id, websocket)
    await websocket.send_json({"type": "ready", "public_id": public_id})
    if not was_online:
        await notify_presence(user_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            if payload.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if payload.get("type") == "group_typing":
                group_public_id = str(payload.get("group_id", "")).strip().upper()
                with SessionLocal() as db:
                    group = db.scalar(
                        select(ChatGroup).where(ChatGroup.public_id == group_public_id)
                    )
                    if not group or not group_membership(db, group.id, user_id):
                        await websocket.send_json(
                            {"type": "error", "detail": "Group not found"}
                        )
                        continue
                    member_ids = group_member_ids(db, group.id)
                event = {
                    "type": "group_typing",
                    "group_id": group_public_id,
                    "from": public_id,
                    "name": current_user.name,
                    "is_typing": payload.get("is_typing") is True,
                }
                for member_id in member_ids:
                    if member_id != user_id:
                        await manager.send_to_user(member_id, event)
                continue
            if payload.get("type") == "typing":
                recipient_public_id = str(payload.get("recipient_id", "")).strip().upper()
                with SessionLocal() as db:
                    recipient = db.scalar(
                        select(User).where(User.public_id == recipient_public_id)
                    )
                    if (
                        not recipient
                        or not connection_between(db, user_id, recipient.id)
                    ):
                        await websocket.send_json(
                            {"type": "error", "detail": "Recipient is not a connection"}
                        )
                        continue
                    recipient_id = recipient.id
                await manager.send_to_user(
                    recipient_id,
                    {
                        "type": "typing",
                        "from": public_id,
                        "is_typing": payload.get("is_typing") is True,
                    },
                )
                continue
            if payload.get("type") == "group_message":
                content = str(payload.get("content", "")).strip()
                group_public_id = str(payload.get("group_id", "")).strip().upper()
                reply_to_id = payload.get("reply_to_id")
                if not content or len(content) > 4000:
                    await websocket.send_json(
                        {"type": "error", "detail": "Message must be 1-4000 characters"}
                    )
                    continue
                with SessionLocal() as db:
                    sender = db.get(User, user_id)
                    group = db.scalar(
                        select(ChatGroup).where(ChatGroup.public_id == group_public_id)
                    )
                    if (
                        not sender
                        or not group
                        or not group_membership(db, group.id, user_id)
                    ):
                        await websocket.send_json(
                            {"type": "error", "detail": "Group not found"}
                        )
                        continue
                    if reply_to_id is not None and (
                        not isinstance(reply_to_id, int)
                        or not group_reply_target(db, reply_to_id, group.id)
                    ):
                        await websocket.send_json(
                            {"type": "error", "detail": "Reply message is not in this group"}
                        )
                        continue
                    message = GroupMessage(
                        group_id=group.id,
                        sender_id=sender.id,
                        content=content,
                        kind="text",
                        reply_to_id=reply_to_id,
                    )
                    db.add(message)
                    db.commit()
                    db.refresh(message)
                    member_ids = group_member_ids(db, group.id)
                    event = {
                        "type": "group_message",
                        "group_id": group.public_id,
                        "message": group_message_json(message, sender, db),
                    }
                for member_id in member_ids:
                    await manager.send_to_user(member_id, event)
                continue
            if payload.get("type") != "message":
                await websocket.send_json({"type": "error", "detail": "Unknown event type"})
                continue

            content = str(payload.get("content", "")).strip()
            recipient_public_id = str(payload.get("recipient_id", "")).strip().upper()
            reply_to_id = payload.get("reply_to_id")
            if not content or len(content) > 4000:
                await websocket.send_json({"type": "error", "detail": "Message must be 1-4000 characters"})
                continue

            with SessionLocal() as db:
                sender = db.get(User, user_id)
                recipient = db.scalar(select(User).where(User.public_id == recipient_public_id))
                if not sender or not recipient or not connection_between(db, sender.id, recipient.id):
                    await websocket.send_json({"type": "error", "detail": "Recipient is not a connection"})
                    continue
                if reply_to_id is not None and (
                    not isinstance(reply_to_id, int)
                    or not private_reply_target(db, reply_to_id, sender.id, recipient.id)
                ):
                    await websocket.send_json(
                        {"type": "error", "detail": "Reply message is not in this chat"}
                    )
                    continue
                message = Message(
                    sender_id=sender.id,
                    receiver_id=recipient.id,
                    content=content,
                    reply_to_id=reply_to_id,
                )
                db.add(message)
                db.commit()
                db.refresh(message)
                event = {
                    "type": "message",
                    "message": message_json(message, sender.public_id, recipient.public_id, db),
                }
                recipient_id = recipient.id
            await manager.send_to_user(user_id, event)
            await manager.send_to_user(recipient_id, event)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
        if not manager.is_online(user_id):
            await notify_presence(user_id)


if (FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

from .message_actions import router as actions_router
app.include_router(actions_router)
