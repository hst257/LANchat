import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, ImagePlus, LoaderCircle, MoreVertical, Send, UserPlus, UsersRound, WifiOff, X } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Avatar, EmptyState, Modal, Toast } from "../components";
import { useSocket } from "../useSocket";
import MessageActions from "../MessageActions";
import { EmojiPicker, ForwardModal, ReplyQuote } from "../ChatFeatures";

const MAX_IMAGE_SIZE = 8 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

const timeLabel = (iso) => new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(iso));
const dayLabel = (iso) => {
  const date = new Date(iso);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return "Today";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
};

export default function GroupChatPage() {
  const { groupId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [group, setGroup] = useState(null);
  const [messages, setMessages] = useState([]);
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [image, setImage] = useState(null);
  const [imagePreview, setImagePreview] = useState("");
  const [sendingImage, setSendingImage] = useState(false);
  const [typingUsers, setTypingUsers] = useState({});
  const [addOpen, setAddOpen] = useState(false);
  const [selectedMembers, setSelectedMembers] = useState(new Set());
  const [addingMembers, setAddingMembers] = useState(false);
  const [replyingTo, setReplyingTo] = useState(null);
  const [forwarding, setForwarding] = useState(null);
  const [lightbox, setLightbox] = useState(null);
  const [toast, setToast] = useState(null);
  const endRef = useRef(null);
  const fileRef = useRef(null);
  const textRef = useRef(null);
  const typingRef = useRef(false);
  const typingTimer = useRef(null);
  const remoteTypingTimers = useRef({});

  const addMessage = useCallback((message) => {
    setMessages((items) => items.some((item) => item.id === message.id) ? items : [...items, message]);
  }, []);

  const loadConversation = useCallback(async () => {
    const data = await api(`/api/groups/${encodeURIComponent(groupId)}/messages`);
    setGroup(data.group);
    setMessages(data.messages);
    document.title = `${data.group.name} · LAN Chat`;
  }, [groupId]);

  useEffect(() => {
    let active = true;
    Promise.all([
      api(`/api/groups/${encodeURIComponent(groupId)}/messages`),
      api("/api/connections"),
    ]).then(([groupData, connectionData]) => {
      if (!active) return;
      setGroup(groupData.group);
      setMessages(groupData.messages);
      setConnections(connectionData.connections);
      document.title = `${groupData.group.name} · LAN Chat`;
    }).catch((error) => setToast({ message: error.message })).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [groupId]);

  const onSocketEvent = useCallback((event) => {
    if (event.type === "group_message_updated" && event.group_id === groupId) {
      setMessages(items => items.map(item => item.id === event.message.id ? event.message : item));
      setLightbox(current => current?.id === event.message.id ? null : current);
    }
    if (event.type === "group_message" && event.group_id === groupId) {
      addMessage(event.message);
      setTypingUsers((current) => { const next = { ...current }; delete next[event.message.sender_id]; return next; });
    }
    if (event.type === "group_typing" && event.group_id === groupId) {
      window.clearTimeout(remoteTypingTimers.current[event.from]);
      setTypingUsers((current) => {
        const next = { ...current };
        if (event.is_typing) next[event.from] = event.name;
        else delete next[event.from];
        return next;
      });
      if (event.is_typing) {
        remoteTypingTimers.current[event.from] = window.setTimeout(() => {
          setTypingUsers((current) => { const next = { ...current }; delete next[event.from]; return next; });
        }, 2200);
      }
    }
    if (event.type === "presence") {
      setGroup((current) => {
        if (!current?.members.some((member) => member.public_id === event.public_id)) return current;
        const members = current.members.map((member) => member.public_id === event.public_id ? { ...member, online: event.online } : member);
        return { ...current, members, online_count: members.filter((member) => member.online).length };
      });
    }
    if (event.type === "group_members_changed" && event.group_id === groupId) loadConversation().catch(() => {});
    if (event.type === "error") setToast({ message: event.detail });
  }, [addMessage, groupId, loadConversation]);
  const { connected, send } = useSocket(onSocketEvent);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: messages.length > 1 ? "smooth" : "auto" }); }, [messages, typingUsers, imagePreview]);
  useEffect(() => () => {
    window.clearTimeout(typingTimer.current);
    Object.values(remoteTypingTimers.current).forEach(window.clearTimeout);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
  }, [imagePreview]);

  const stopTyping = useCallback(() => {
    window.clearTimeout(typingTimer.current);
    if (typingRef.current) {
      send({ type: "group_typing", group_id: groupId, is_typing: false });
      typingRef.current = false;
    }
  }, [groupId, send]);

  const changeText = (event) => {
    const value = event.target.value;
    setText(value);
    if (value.trim() && !typingRef.current && send({ type: "group_typing", group_id: groupId, is_typing: true })) typingRef.current = true;
    window.clearTimeout(typingTimer.current);
    typingTimer.current = window.setTimeout(stopTyping, 1100);
  };

  const selectImage = (selected) => {
    if (!selected) return;
    if (!ALLOWED_TYPES.has(selected.type)) { setToast({ message: "Choose a PNG, JPEG, GIF, or WebP image" }); return; }
    if (selected.size > MAX_IMAGE_SIZE) { setToast({ message: "Images must be 8 MB or smaller" }); return; }
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImage(selected);
    setImagePreview(URL.createObjectURL(selected));
  };

  const clearImage = () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImage(null);
    setImagePreview("");
  };

  const pasteImage = (event) => {
    const item = Array.from(event.clipboardData?.items || []).find((entry) => entry.type.startsWith("image/"));
    const pastedImage = item?.getAsFile();
    if (!pastedImage) return;
    event.preventDefault();
    selectImage(pastedImage);
    setToast({ kind: "success", message: "Screenshot pasted — press Send when ready" });
  };

  const startReply = (message) => {
    setReplyingTo({
      ...message,
      sender_name: message.sender_id === user.public_id ? "You" : message.sender_name,
    });
    window.setTimeout(() => textRef.current?.focus(), 0);
  };

  const addEmoji = (emoji) => {
    setText((current) => current.length + emoji.length <= (image ? 1000 : 4000) ? `${current}${emoji}` : current);
    window.setTimeout(() => textRef.current?.focus(), 0);
  };

  const submit = async (event) => {
    event.preventDefault();
    const content = text.trim();
    if (!content && !image) return;
    stopTyping();
    if (image) {
      const form = new FormData();
      form.append("image", image);
      form.append("caption", content);
      if (replyingTo) form.append("reply_to_id", String(replyingTo.id));
      setSendingImage(true);
      try {
        const data = await api(`/api/groups/${encodeURIComponent(groupId)}/images`, { method: "POST", body: form });
        addMessage(data.message);
        setText("");
        setReplyingTo(null);
        clearImage();
      } catch (error) { setToast({ message: error.message }); }
      finally { setSendingImage(false); }
      return;
    }
    if (!send({ type: "group_message", group_id: groupId, content, reply_to_id: replyingTo?.id || null })) { setToast({ message: "Still reconnecting. Try again in a moment." }); return; }
    setText("");
    setReplyingTo(null);
  };

  const eligibleConnections = useMemo(() => connections.filter((contact) => !group?.members.some((member) => member.public_id === contact.public_id)), [connections, group]);
  const groupedMessages = useMemo(() => messages.map((message, index) => ({
    message,
    showDay: index === 0 || dayLabel(messages[index - 1].created_at) !== dayLabel(message.created_at),
    showSender: message.sender_id !== user.public_id && (index === 0 || messages[index - 1].sender_id !== message.sender_id),
  })), [messages, user.public_id]);
  const typingNames = Object.values(typingUsers);
  const typingLabel = typingNames.length > 1 ? `${typingNames.length} people are typing` : typingNames.length === 1 ? `${typingNames[0]} is typing` : "";

  const toggleMember = (publicId) => setSelectedMembers((current) => { const next = new Set(current); next.has(publicId) ? next.delete(publicId) : next.add(publicId); return next; });
  const addMembers = async (event) => {
    event.preventDefault();
    if (!selectedMembers.size) return;
    setAddingMembers(true);
    try {
      const data = await api(`/api/groups/${encodeURIComponent(groupId)}/members`, { method: "POST", body: JSON.stringify({ member_ids: [...selectedMembers] }) });
      setGroup(data.group);
      setSelectedMembers(new Set());
      setAddOpen(false);
      setToast({ kind: "success", message: "Members added to the group" });
    } catch (error) { setToast({ message: error.message }); }
    finally { setAddingMembers(false); }
  };

  return (
    <main className="chat-page-react">
      <section className="chat-window">
        <header className="chat-topbar">
          <button className="icon-button" onClick={() => navigate("/connections")} aria-label="Back"><ArrowLeft size={21} /></button>
          <span className="group-avatar"><UsersRound size={20} /></span>
          <div className="chat-contact"><strong>{group?.name || "Loading group…"}</strong><span className={typingLabel ? "typing-status" : ""}>{typingLabel || (group ? `${group.member_count} members · ${group.online_count} online` : "")}</span></div>
          <span className={`socket-state ${connected ? "online" : ""}`}>{connected ? "Connected" : <><WifiOff size={14} /> Reconnecting</>}</span>
          {group?.role === "owner" && <button className="icon-button" onClick={() => setAddOpen(true)} aria-label="Add group member"><UserPlus size={20} /></button>}
          <button className="icon-button" aria-label="Group options"><MoreVertical size={20} /></button>
        </header>

        <div className="message-canvas">
          <div className="chat-watermark"><UsersRound size={17} />{group?.member_count || ""} members in this group</div>
          {loading ? <div className="messages-loading"><LoaderCircle className="spin" /><span>Loading messages…</span></div> : messages.length === 0 ? <div className="conversation-start"><span><UsersRound size={26} /></span><h2>{group?.name}</h2><p>This is the beginning of the group. Send a message or paste a screenshot to get started.</p></div> : groupedMessages.map(({ message, showDay, showSender }) => (
            <Fragment key={message.id}>
              {showDay && <div className="day-divider"><span>{dayLabel(message.created_at)}</span></div>}
              <article className={`message-bubble ${message.sender_id === user.public_id ? "mine" : "theirs"}`}>
                {showSender && <button type="button" className="message-sender" onClick={() => navigate(`/profile/${message.sender_id}`)} title={`View ${message.sender_name}'s profile`}>{message.sender_name}</button>}
                {message.forwarded && <span className="forwarded-label">Forwarded</span>}
                <ReplyQuote reply={message.reply_to} />
                {message.kind === "image" && <button className="message-image" onClick={() => setLightbox(message)}><img src={message.image_url} alt={message.content || "Shared image"} loading="lazy" /></button>}
                {message.kind === "deleted" ? <p className="deleted-message">Message deleted</p> : message.content && <p>{message.content}</p>}
                {message.edited && <small>Edited</small>}
                <MessageActions message={message} scope="group" canModify={message.sender_id === user.public_id} onReply={startReply} onForward={setForwarding} onUpdate={updated => setMessages(items => items.map(item => item.id === updated.id ? updated : item))} />
                <time>{timeLabel(message.created_at)}</time>
              </article>
            </Fragment>
          ))}
          {typingNames.length > 0 && <div className="typing-bubble"><i /><i /><i /></div>}
          <div ref={endRef} />
        </div>

        {replyingTo && <ReplyQuote reply={replyingTo} composing onClose={() => setReplyingTo(null)} />}
        {imagePreview && <div className="composer-preview"><img src={imagePreview} alt="Ready to send" /><div><strong>{image.name}</strong><small>Ready to share with {group?.name}</small></div><button className="icon-button" onClick={clearImage}><X size={18} /></button></div>}
        <form className="message-composer" onSubmit={submit}>
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={(event) => { selectImage(event.target.files?.[0]); event.target.value = ""; }} hidden />
          <button type="button" className="icon-button composer-action" onClick={() => fileRef.current?.click()} aria-label="Attach image"><ImagePlus size={21} /></button>
          <div className="message-input-shell"><textarea ref={textRef} value={text} onChange={changeText} onPaste={pasteImage} onBlur={stopTyping} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form.requestSubmit(); } }} placeholder={image ? "Add a caption…" : "Message the group or paste a screenshot…"} maxLength={image ? 1000 : 4000} rows="1" /><EmojiPicker className="composer-emoji-picker" onSelect={addEmoji} /></div>
          <button className="send-button" disabled={sendingImage || (!text.trim() && !image)}>{sendingImage ? <LoaderCircle className="spin" size={20} /> : <Send size={20} />}</button>
        </form>
      </section>

      {addOpen && <Modal title="Add group members" onClose={() => setAddOpen(false)}><form className="modal-body group-form" onSubmit={addMembers}><p className="modal-intro">Choose more of your connections to add to {group?.name}.</p><div className="member-picker">{eligibleConnections.length === 0 ? <EmptyState icon={UsersRound} title="Everyone is here" detail="All your connections are already members." /> : eligibleConnections.map((contact) => <label className={`member-option ${selectedMembers.has(contact.public_id) ? "selected" : ""}`} key={contact.public_id}><input type="checkbox" checked={selectedMembers.has(contact.public_id)} onChange={() => toggleMember(contact.public_id)} /><Avatar name={contact.name} src={contact.avatar_url} online={contact.online} /><span><strong>{contact.name}</strong><small>@{contact.username}</small></span><i>{selectedMembers.has(contact.public_id) && <Check size={14} />}</i></label>)}</div><button className="primary-button group-create-button" disabled={!selectedMembers.size || addingMembers}>{addingMembers ? "Adding…" : `Add ${selectedMembers.size || ""} member${selectedMembers.size === 1 ? "" : "s"}`}</button></form></Modal>}
      {lightbox && <div className="lightbox" onMouseDown={(event) => event.target === event.currentTarget && setLightbox(null)}><button className="lightbox-close" onClick={() => setLightbox(null)}><X size={24} /></button><img src={lightbox.image_url} alt={lightbox.content || "Shared image"} />{lightbox.content && <p>{lightbox.content}</p>}</div>}
      {forwarding && <ForwardModal message={forwarding} scope="group" onClose={() => setForwarding(null)} onDone={(name, forwardedMessage, targetType, targetId) => { if (targetType === "group" && targetId === groupId) addMessage(forwardedMessage); setForwarding(null); setToast({ kind: "success", message: `Forwarded to ${name}` }); }} />}
      <Toast toast={toast} onClose={() => setToast(null)} />
    </main>
  );
}
