import { useEffect, useRef, useState } from "react";
import { Forward, Image, LoaderCircle, Plus, Reply, Smile, UsersRound, Video, X } from "lucide-react";
import { api } from "./api";
import { Avatar, EmptyState, Modal } from "./components";

export const EMOJIS = ["😀", "😂", "😍", "🥰", "😎", "🤔", "😢", "😡", "👍", "👏", "🙏", "🎉", "🔥", "❤️", "✅", "👋"];
export const REACTION_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🙏", "🎉", "🔥"];

export function EmojiPicker({ onSelect, reactions = false, className = "" }) {
  const [open, setOpen] = useState(false);
  const pickerRef = useRef(null);
  const items = reactions ? REACTION_EMOJIS : EMOJIS;

  useEffect(() => {
    const close = (event) => {
      if (!pickerRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  return (
    <span className={`emoji-picker ${open ? "open" : ""} ${className}`} ref={pickerRef}>
      <button type="button" className={reactions ? "reaction-add-button" : "emoji-trigger"} onClick={() => setOpen((value) => !value)} aria-label={reactions ? "React to message" : "Add emoji"} aria-expanded={open}>
        {reactions ? <Plus size={15} /> : <Smile size={20} />}
      </button>
      {open && <span className="emoji-palette" role="menu" aria-label={reactions ? "Message reactions" : "Emojis"}>
        {items.map((emoji) => <button type="button" role="menuitem" key={emoji} onClick={() => { onSelect(emoji); setOpen(false); }}>{emoji}</button>)}
      </span>}
    </span>
  );
}

export function ReplyQuote({ reply, composing = false, onClose }) {
  if (!reply) return null;
  const summary = reply.kind === "deleted" ? "Deleted message" : reply.content || (reply.kind === "image" ? "Photo" : reply.kind === "video" ? "Video" : "Message");
  return (
    <div className={composing ? "composer-reply" : "message-reply-quote"}>
      <Reply size={15} />
      <span><strong>{reply.sender_name}</strong><small>{reply.kind === "image" && <Image size={12} />}{reply.kind === "video" && <Video size={12} />}{summary}</small></span>
      {onClose && <button type="button" onClick={onClose} aria-label="Cancel reply"><X size={17} /></button>}
    </div>
  );
}

export function ForwardModal({ message, scope, onClose, onDone }) {
  const [connections, setConnections] = useState([]);
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyTarget, setBusyTarget] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api("/api/connections"), api("/api/groups")])
      .then(([people, groupData]) => {
        setConnections(people.connections);
        setGroups(groupData.groups);
      })
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false));
  }, []);

  const forwardTo = async (targetType, targetId, targetName) => {
    const key = `${targetType}:${targetId}`;
    setBusyTarget(key);
    setError("");
    try {
      const data = await api(`/api/message-actions/${scope}/${message.id}/forward`, {
        method: "POST",
        body: JSON.stringify({ target_type: targetType, target_id: targetId }),
      });
      onDone(targetName, data.message, targetType, targetId);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusyTarget("");
    }
  };

  return (
    <Modal title="Forward message" onClose={onClose}>
      <div className="modal-body forward-modal-body">
        <div className="forward-preview"><Forward size={16} /><span>{message.kind === "image" ? "Photo" : message.kind === "video" ? "Video" : message.content}</span></div>
        {loading ? <div className="forward-loading"><LoaderCircle className="spin" />Loading destinations…</div> : connections.length + groups.length === 0 ? <EmptyState icon={Forward} title="Nowhere to forward" detail="Add a connection or join a group first." /> : <div className="forward-destinations">
          {connections.length > 0 && <><h3>Connections</h3>{connections.map((contact) => <button type="button" key={contact.public_id} disabled={Boolean(busyTarget)} onClick={() => forwardTo("private", contact.public_id, contact.name)}><Avatar name={contact.name} src={contact.avatar_url} size="small" /><span><strong>{contact.name}</strong><small>@{contact.username}</small></span>{busyTarget === `private:${contact.public_id}` ? <LoaderCircle className="spin" size={17} /> : <Forward size={16} />}</button>)}</>}
          {groups.length > 0 && <><h3>Groups</h3>{groups.map((group) => <button type="button" key={group.public_id} disabled={Boolean(busyTarget)} onClick={() => forwardTo("group", group.public_id, group.name)}><span className="group-avatar small"><UsersRound size={16} /></span><span><strong>{group.name}</strong><small>{group.member_count} members</small></span>{busyTarget === `group:${group.public_id}` ? <LoaderCircle className="spin" size={17} /> : <Forward size={16} />}</button>)}</>}
        </div>}
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
    </Modal>
  );
}
