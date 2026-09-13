from datetime import datetime, timezone
from pathlib import Path
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import (
    User,
    Message,
    GroupMessage,
    ChatGroup,
    Connection,
    ConnectionRequest,
    GroupMember,
    MessageReaction,
    GroupMessageReaction,
)
from . import main

router = APIRouter()


class EditInput(BaseModel):
    content: str = Field(max_length=4000)


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    status_text: str = Field(default="Available", max_length=80)
    bio: str = Field(default="", max_length=280)

    @field_validator("name", "status_text", "bio", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReactionInput(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


class ForwardInput(BaseModel):
    target_type: Literal["private", "group"]
    target_id: str = Field(min_length=1, max_length=24)


REACTION_EMOJIS = {"👍", "❤️", "😂", "😮", "😢", "🙏", "🎉", "🔥"}


def accessible_message(scope: str, message_id: int, user: User, db: Session):
    if scope == "private":
        message = db.get(Message, message_id)
        if not message or user.id not in (message.sender_id, message.receiver_id):
            raise HTTPException(404, "Message not found")
        return message
    if scope == "group":
        message = db.get(GroupMessage, message_id)
        if not message or not main.group_membership(db, message.group_id, user.id):
            raise HTTPException(404, "Message not found")
        return message
    raise HTTPException(404, "Message not found")


def updated_payload(scope: str, message, db: Session) -> tuple[dict, list[int], dict]:
    sender = db.get(User, message.sender_id)
    if scope == "private":
        receiver = db.get(User, message.receiver_id)
        payload = main.message_json(
            message, sender.public_id, receiver.public_id, db
        )
        return payload, [sender.id, receiver.id], {
            "type": "message_updated", "message": payload
        }
    group = db.get(ChatGroup, message.group_id)
    payload = main.group_message_json(message, sender, db)
    return payload, main.group_member_ids(db, group.id), {
        "type": "group_message_updated",
        "group_id": group.public_id,
        "message": payload,
    }


def relationship_with(db: Session, current_user: User, target: User) -> str:
    if current_user.id == target.id:
        return "self"
    if main.connection_between(db, current_user.id, target.id):
        return "connected"
    request = db.scalar(
        select(ConnectionRequest).where(
            ConnectionRequest.status == "pending",
            or_(
                and_(
                    ConnectionRequest.sender_id == current_user.id,
                    ConnectionRequest.receiver_id == target.id,
                ),
                and_(
                    ConnectionRequest.sender_id == target.id,
                    ConnectionRequest.receiver_id == current_user.id,
                ),
            ),
        )
    )
    if not request:
        return "none"
    return "sent" if request.sender_id == current_user.id else "incoming"


def profile_json(db: Session, current_user: User, target: User) -> dict:
    current_groups = set(
        db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == current_user.id))
    )
    target_groups = set(
        db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == target.id))
    )
    return {
        **main.user_json(target),
        "created_at": target.created_at.isoformat(),
        "online": main.manager.is_online(target.id),
        "relationship": relationship_with(db, current_user, target),
        "mutual_group_count": len(current_groups & target_groups),
    }


async def change_message(scope, message_id, content, user, db):
    if scope not in ("private", "group"):
        raise HTTPException(404, "Message not found")
    model = Message if scope == "private" else GroupMessage
    message = db.scalar(select(model).where(model.id == message_id).with_for_update())
    if not message or message.sender_id != user.id:
        raise HTTPException(404, "Message not found")
    if scope == "group" and not main.group_membership(db, message.group_id, user.id):
        raise HTTPException(403, "Group membership required")
    if message.kind == "deleted":
        raise HTTPException(409, "Message was already deleted")
    deleted_media = None
    if content is None:
        deleted_media = message.media_filename
        message.kind = "deleted"
        message.content = ""
        message.edited_at = None
        message.media_filename = None
        message.media_mime = None
        message.original_filename = None
        reaction_model = MessageReaction if scope == "private" else GroupMessageReaction
        db.execute(delete(reaction_model).where(reaction_model.message_id == message.id))
    else:
        content = content.strip()
        if message.kind == "text" and not content:
            raise HTTPException(422, "Text messages cannot be empty")
        if message.kind == "image" and len(content) > 1000:
            raise HTTPException(422, "Captions must be 1000 characters or fewer")
        message.content = content
        message.edited_at = datetime.now(timezone.utc)
    db.commit()
    if deleted_media:
        (main.UPLOAD_DIR / deleted_media).unlink(missing_ok=True)
    payload, recipients, event = updated_payload(scope, message, db)
    for recipient_id in recipients:
        await main.manager.send_to_user(recipient_id, event)
    return {"message": payload}


@router.patch("/api/message-actions/{scope}/{message_id}")
async def edit_message(scope: str, message_id: int, payload: EditInput,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return await change_message(scope, message_id, payload.content, user, db)


@router.delete("/api/message-actions/{scope}/{message_id}")
async def delete_message(scope: str, message_id: int,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return await change_message(scope, message_id, None, user, db)


@router.post("/api/message-actions/{scope}/{message_id}/reactions")
async def react_to_message(
    scope: str,
    message_id: int,
    payload: ReactionInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.emoji not in REACTION_EMOJIS:
        raise HTTPException(422, "Choose a supported reaction")
    message = accessible_message(scope, message_id, user, db)
    if message.kind == "deleted":
        raise HTTPException(409, "Cannot react to a deleted message")
    model = MessageReaction if scope == "private" else GroupMessageReaction
    reaction = db.scalar(
        select(model).where(model.message_id == message.id, model.user_id == user.id)
    )
    if reaction and reaction.emoji == payload.emoji:
        db.delete(reaction)
    elif reaction:
        reaction.emoji = payload.emoji
    else:
        db.add(model(message_id=message.id, user_id=user.id, emoji=payload.emoji))
    db.commit()
    response, recipients, event = updated_payload(scope, message, db)
    for recipient_id in recipients:
        await main.manager.send_to_user(recipient_id, event)
    return {"message": response}


@router.post("/api/message-actions/{scope}/{message_id}/forward", status_code=201)
async def forward_message(
    scope: str,
    message_id: int,
    payload: ForwardInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = accessible_message(scope, message_id, user, db)
    if source.kind == "deleted":
        raise HTTPException(409, "Cannot forward a deleted message")

    copied_filename = None
    if source.kind == "image":
        if not source.media_filename:
            raise HTTPException(404, "Image file not found")
        source_path = main.UPLOAD_DIR / source.media_filename
        if not source_path.is_file():
            raise HTTPException(404, "Image file not found")
        copied_filename = f"{secrets.token_hex(24)}{Path(source.media_filename).suffix}"
        (main.UPLOAD_DIR / copied_filename).write_bytes(source_path.read_bytes())

    if payload.target_type == "private":
        target = db.scalar(
            select(User).where(User.public_id == payload.target_id.upper())
        )
        if not target or not main.connection_between(db, user.id, target.id):
            if copied_filename:
                (main.UPLOAD_DIR / copied_filename).unlink(missing_ok=True)
            raise HTTPException(404, "Connection not found")
        forwarded = Message(
            sender_id=user.id,
            receiver_id=target.id,
            content=source.content,
            kind=source.kind,
            media_filename=copied_filename,
            media_mime=source.media_mime,
            original_filename=source.original_filename,
            forwarded=True,
        )
        db.add(forwarded)
        recipients = [user.id, target.id]
    else:
        target = db.scalar(
            select(ChatGroup).where(ChatGroup.public_id == payload.target_id.upper())
        )
        if not target or not main.group_membership(db, target.id, user.id):
            if copied_filename:
                (main.UPLOAD_DIR / copied_filename).unlink(missing_ok=True)
            raise HTTPException(404, "Group not found")
        forwarded = GroupMessage(
            group_id=target.id,
            sender_id=user.id,
            content=source.content,
            kind=source.kind,
            media_filename=copied_filename,
            media_mime=source.media_mime,
            original_filename=source.original_filename,
            forwarded=True,
        )
        db.add(forwarded)
        recipients = main.group_member_ids(db, target.id)
    try:
        db.commit()
        db.refresh(forwarded)
    except Exception:
        db.rollback()
        if copied_filename:
            (main.UPLOAD_DIR / copied_filename).unlink(missing_ok=True)
        raise

    if payload.target_type == "private":
        result = main.message_json(forwarded, user.public_id, target.public_id, db)
        event = {"type": "message", "message": result}
    else:
        result = main.group_message_json(forwarded, user, db)
        event = {"type": "group_message", "group_id": target.public_id, "message": result}
    for recipient_id in recipients:
        await main.manager.send_to_user(recipient_id, event)
    return {"message": result}


@router.post("/api/me/avatar")
async def upload_avatar(image: UploadFile = File(...), user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    data = await image.read(main.MAX_IMAGE_BYTES + 1)
    if len(data) > main.MAX_IMAGE_BYTES:
        raise HTTPException(413, "Profile photos must be 8 MB or smaller")
    detected = main.image_format(data)
    if not detected:
        raise HTTPException(415, "Use a PNG, JPEG, GIF, or WebP image")
    mime, extension = detected
    filename = f"avatar-{secrets.token_hex(24)}{extension}"
    path = main.UPLOAD_DIR / filename
    path.write_bytes(data)
    old_filename = user.avatar_filename
    user.avatar_filename, user.avatar_mime = filename, mime
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    if old_filename and old_filename != filename:
        (main.UPLOAD_DIR / old_filename).unlink(missing_ok=True)
    payload = main.user_json(user)
    for recipient_id in list(main.manager.active):
        await main.manager.send_to_user(recipient_id, {"type": "profile_updated", "user": payload})
    return {"user": payload}


@router.get("/api/profiles/{public_id}")
def get_profile(public_id: str, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    target = db.scalar(select(User).where(User.public_id == public_id.upper()))
    if not target:
        raise HTTPException(404, "Profile not found")
    return {"profile": profile_json(db, user, target)}


@router.patch("/api/me/profile")
async def update_profile(payload: ProfileInput, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    user.name = payload.name
    user.status_text = payload.status_text or "Available"
    user.bio = payload.bio
    db.commit()
    response = main.user_json(user)
    for recipient_id in list(main.manager.active):
        await main.manager.send_to_user(
            recipient_id, {"type": "profile_updated", "user": response}
        )
    return {"user": response}


@router.delete("/api/connections/{public_id}")
async def disconnect_user(public_id: str, user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    target = db.scalar(select(User).where(User.public_id == public_id.upper()))
    if not target:
        raise HTTPException(404, "Connection not found")
    connection = main.connection_between(db, user.id, target.id)
    if not connection:
        raise HTTPException(404, "Connection not found")
    private_pair = or_(
        and_(Message.sender_id == user.id, Message.receiver_id == target.id),
        and_(Message.sender_id == target.id, Message.receiver_id == user.id),
    )
    media_files = [
        row.media_filename
        for row in db.scalars(select(Message).where(private_pair))
        if row.media_filename
    ]
    db.execute(delete(Message).where(private_pair))
    db.execute(
        delete(ConnectionRequest).where(
            or_(
                and_(
                    ConnectionRequest.sender_id == user.id,
                    ConnectionRequest.receiver_id == target.id,
                ),
                and_(
                    ConnectionRequest.sender_id == target.id,
                    ConnectionRequest.receiver_id == user.id,
                ),
            )
        )
    )
    db.delete(connection)
    db.commit()
    for filename in media_files:
        (main.UPLOAD_DIR / filename).unlink(missing_ok=True)
    await main.manager.send_to_user(
        user.id, {"type": "connection_removed", "public_id": target.public_id}
    )
    await main.manager.send_to_user(
        target.id, {"type": "connection_removed", "public_id": user.public_id}
    )
    return {"status": "disconnected"}


@router.get("/api/avatars/{public_id}")
def avatar(public_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.scalar(select(User).where(User.public_id == public_id))
    if not target or not target.avatar_filename:
        raise HTTPException(404, "Profile photo not found")
    path = main.UPLOAD_DIR / target.avatar_filename
    if not path.is_file():
        raise HTTPException(404, "Profile photo not found")
    return FileResponse(path, media_type=target.avatar_mime,
                        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})
