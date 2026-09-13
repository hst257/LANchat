import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, CheckCheck, ImagePlus, LoaderCircle, MessageCircleMore, MoreVertical, Send, WifiOff, X } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Avatar, Toast } from "../components";
import { useSocket } from "../useSocket";
import MessageActions from "../MessageActions";
import { EmojiPicker, ForwardModal, ReplyQuote } from "../ChatFeatures";

const MAX_IMAGE_SIZE = 8 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

function timeLabel(iso) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}

function dayLabel(iso) {
  const date = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: date.getFullYear() !== today.getFullYear() ? "numeric" : undefined }).format(date);
}

export default function ChatPage() {
  const { contactId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [contact, setContact] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [image, setImage] = useState(null);
  const [imagePreview, setImagePreview] = useState("");
  const [sendingImage, setSendingImage] = useState(false);
  const [contactTyping, setContactTyping] = useState(false);
  const [disconnected, setDisconnected] = useState(false);
  const [replyingTo, setReplyingTo] = useState(null);
  const [forwarding, setForwarding] = useState(null);
  const [lightbox, setLightbox] = useState(null);
  const [toast, setToast] = useState(null);
  const endRef = useRef(null);
  const fileRef = useRef(null);
  const textRef = useRef(null);
  const typingRef = useRef(false);
  const typingTimer = useRef(null);
  const incomingTypingTimer = useRef(null);

  const addMessage = useCallback((message) => {
    setMessages((items) => items.some((item) => item.id === message.id) ? items : [...items, message]);
  }, []);

  const markRead = useCallback(() => {
    if (document.visibilityState !== "visible") return;
    api(`/api/chats/${encodeURIComponent(contactId)}/read`, { method: "POST" }).catch(() => {});
  }, [contactId]);

  const onSocketEvent = useCallback((event) => {
    if (event.type === "message_updated") {
      setMessages(items => items.map(item => item.id === event.message.id ? event.message : item));
      setLightbox(current => current?.id === event.message.id ? null : current);
    }
    if (event.type === "messages_read" && event.reader_id === contactId) {
      const readIds = new Set(event.message_ids);
      setMessages((items) => items.map((item) => readIds.has(item.id) ? { ...item, read: true, read_at: event.read_at } : item));
    }
    if (event.type === "profile_updated" && event.user.public_id === contactId) {
      setContact(current => current ? { ...current, ...event.user } : current);
    }
    if (event.type === "connection_removed" && event.public_id === contactId) {
      setMessages([]);
      setContactTyping(false);
      setDisconnected(true);
      setReplyingTo(null);
      setToast({ message: "This connection was removed. Private message history has been deleted." });
    }
    if (event.type === "message" && [event.message.sender_id, event.message.receiver_id].includes(contactId) && [event.message.sender_id, event.message.receiver_id].includes(user.public_id)) {
      addMessage(event.message);
      if (event.message.sender_id === contactId) setContactTyping(false);
      if (event.message.sender_id === contactId) markRead();
    }
    if (event.type === "presence" && event.public_id === contactId) {
      setContact((current) => current ? { ...current, online: event.online } : current);
    }
    if (event.type === "typing" && event.from === contactId) {
      setContactTyping(event.is_typing);
      window.clearTimeout(incomingTypingTimer.current);
      if (event.is_typing) incomingTypingTimer.current = window.setTimeout(() => setContactTyping(false), 2200);
    }
    if (event.type === "error") setToast({ message: event.detail });
  }, [addMessage, contactId, markRead, user.public_id]);
  const { connected, send } = useSocket(onSocketEvent);

  useEffect(() => {
    let active = true;
    api(`/api/chats/${encodeURIComponent(contactId)}/messages`)
      .then((data) => {
        if (!active) return;
        setContact(data.contact);
        setMessages(data.messages);
        setDisconnected(false);
        document.title = `${data.contact.name} · LAN Chat`;
        markRead();
      })
      .catch((error) => setToast({ message: error.message }))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [contactId, markRead]);

  useEffect(() => {
    const readVisibleMessages = () => {
      if (document.visibilityState === "visible") markRead();
    };
    document.addEventListener("visibilitychange", readVisibleMessages);
    window.addEventListener("focus", readVisibleMessages);
    return () => {
      document.removeEventListener("visibilitychange", readVisibleMessages);
      window.removeEventListener("focus", readVisibleMessages);
    };
  }, [markRead]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: messages.length > 1 ? "smooth" : "auto" });
  }, [messages, contactTyping, imagePreview]);

  useEffect(() => () => {
    window.clearTimeout(typingTimer.current);
    window.clearTimeout(incomingTypingTimer.current);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
  }, [imagePreview]);

  const stopTyping = useCallback(() => {
    window.clearTimeout(typingTimer.current);
    if (typingRef.current) {
      send({ type: "typing", recipient_id: contactId, is_typing: false });
      typingRef.current = false;
    }
  }, [contactId, send]);

  const changeText = (event) => {
    const value = event.target.value;
    setText(value);
    if (value.trim() && !typingRef.current) {
      if (send({ type: "typing", recipient_id: contactId, is_typing: true })) typingRef.current = true;
    }
    window.clearTimeout(typingTimer.current);
    typingTimer.current = window.setTimeout(stopTyping, 1100);
  };

  const selectImage = (selected) => {
    if (!selected) return;
    if (!ALLOWED_TYPES.has(selected.type)) {
      setToast({ message: "Choose a PNG, JPEG, GIF, or WebP image" });
      return;
    }
    if (selected.size > MAX_IMAGE_SIZE) {
      setToast({ message: "Images must be 8 MB or smaller" });
      return;
    }
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImage(selected);
    setImagePreview(URL.createObjectURL(selected));
  };

  const chooseImage = (event) => {
    selectImage(event.target.files?.[0]);
    event.target.value = "";
  };

  const pasteImage = (event) => {
    const imageItem = Array.from(event.clipboardData?.items || []).find((item) => item.type.startsWith("image/"));
    const pastedImage = imageItem?.getAsFile();
    if (!pastedImage) return;
    event.preventDefault();
    selectImage(pastedImage);
    setToast({ kind: "success", message: "Screenshot pasted — press Send when ready" });
  };

  const clearImage = () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImage(null);
    setImagePreview("");
  };

  const startReply = (message) => {
    setReplyingTo({
      ...message,
      sender_name: message.sender_id === user.public_id ? "You" : contact?.name || "Contact",
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
      setSendingImage(true);
      const form = new FormData();
      form.append("image", image);
      form.append("caption", content);
      if (replyingTo) form.append("reply_to_id", String(replyingTo.id));
      try {
        const data = await api(`/api/chats/${encodeURIComponent(contactId)}/images`, { method: "POST", body: form });
        addMessage(data.message);
        setText("");
        setReplyingTo(null);
        clearImage();
      } catch (error) {
        setToast({ message: error.message });
      } finally {
        setSendingImage(false);
      }
      return;
    }

    if (!send({ type: "message", recipient_id: contactId, content, reply_to_id: replyingTo?.id || null })) {
      setToast({ message: "Still reconnecting. Try again in a moment." });
      return;
    }
    setText("");
    setReplyingTo(null);
  };

  const groupedMessages = useMemo(() => messages.map((message, index) => ({
    message,
    showDay: index === 0 || dayLabel(messages[index - 1].created_at) !== dayLabel(message.created_at),
    startsGroup: index === 0 || messages[index - 1].sender_id !== message.sender_id || message.kind === "image",
  })), [messages]);

  return (
    <main className="chat-page-react">
      <section className="chat-window">
        <header className="chat-topbar">
          <button className="icon-button" onClick={() => navigate("/connections")} aria-label="Back"><ArrowLeft size={21} /></button>
          <button type="button" className="chat-profile-link" onClick={() => contact && navigate(`/profile/${contact.public_id}`)} disabled={!contact} title="View profile">
            {contact ? <Avatar name={contact.name} src={contact.avatar_url} online={contact.online} /> : <span className="skeleton circle" />}
            <span className="chat-contact">
              <strong>{contact?.name || "Loading conversation…"}</strong>
              <span className={contactTyping ? "typing-status" : ""}>{contactTyping ? <><i /><i /><i /> typing</> : contact?.online ? "Online" : "Offline"}</span>
            </span>
          </button>
          <span className={`socket-state ${connected ? "online" : ""}`}>{connected ? "Connected" : <><WifiOff size={14} /> Reconnecting</>}</span>
          <button className="icon-button" aria-label="Conversation options"><MoreVertical size={20} /></button>
        </header>

        <div className="message-canvas">
          <div className="chat-watermark"><MessageCircleMore size={18} />Messages stay on your LAN server</div>
          {loading ? <div className="messages-loading"><LoaderCircle className="spin" /><span>Loading messages…</span></div> : messages.length === 0 ? <div className="conversation-start"><span><MessageCircleMore size={26} /></span><h2>{disconnected ? "Connection removed" : "Start the conversation"}</h2><p>{disconnected ? "Private history was deleted. Visit this profile to reconnect." : `Say hello to ${contact?.name}. Messages appear instantly on connected devices.`}</p>{disconnected && <button type="button" className="secondary-button" onClick={() => navigate(`/profile/${contactId}`)}>View profile</button>}</div> : groupedMessages.map(({ message, showDay, startsGroup }) => (
            <Fragment key={message.id}>
              {showDay && <div className="day-divider"><span>{dayLabel(message.created_at)}</span></div>}
              <article className={`message-bubble ${message.sender_id === user.public_id ? "mine" : "theirs"} ${startsGroup ? "group-start" : ""}`}>
                {message.forwarded && <span className="forwarded-label">Forwarded</span>}
                <ReplyQuote reply={message.reply_to} />
                {message.kind === "image" && <button className="message-image" onClick={() => setLightbox(message)} aria-label={`Open ${message.original_filename || "image"}`}><img src={message.image_url} alt={message.content || "Shared image"} loading="lazy" /></button>}
                {message.kind === "deleted" ? <p className="deleted-message">Message deleted</p> : message.content && <p>{message.content}</p>}
                {message.edited && <small>Edited</small>}
                <MessageActions message={message} scope="private" canModify={message.sender_id === user.public_id} onReply={startReply} onForward={setForwarding} onUpdate={updated => setMessages(items => items.map(item => item.id === updated.id ? updated : item))} />
                <span className="message-meta"><time dateTime={message.created_at}>{timeLabel(message.created_at)}</time>{message.sender_id === user.public_id && <span className={`read-receipt ${message.read ? "read" : ""}`} title={message.read ? "Read" : "Delivered"} aria-label={message.read ? "Read" : "Delivered"}><CheckCheck size={13} /></span>}</span>
              </article>
            </Fragment>
          ))}
          {contactTyping && <div className="typing-bubble"><i /><i /><i /></div>}
          <div ref={endRef} />
        </div>

        {replyingTo && <ReplyQuote reply={replyingTo} composing onClose={() => setReplyingTo(null)} />}
        {imagePreview && <div className="composer-preview"><img src={imagePreview} alt="Ready to send" /><div><strong>{image.name}</strong><small>{(image.size / 1024 / 1024).toFixed(1)} MB · Add a caption below</small></div><button className="icon-button" onClick={clearImage} aria-label="Remove image"><X size={18} /></button></div>}

        <form className="message-composer" onSubmit={submit}>
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={chooseImage} hidden />
          <button type="button" className="icon-button composer-action" disabled={disconnected} onClick={() => fileRef.current?.click()} aria-label="Attach image"><ImagePlus size={21} /></button>
          <div className="message-input-shell"><textarea ref={textRef} value={text} onChange={changeText} onPaste={pasteImage} onBlur={stopTyping} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form.requestSubmit(); } }} placeholder={disconnected ? "Reconnect to send messages" : image ? "Add a caption…" : "Write a message or paste a screenshot…"} rows="1" maxLength={image ? 1000 : 4000} disabled={disconnected} /><EmojiPicker className="composer-emoji-picker" onSelect={addEmoji} /></div>
          <button className="send-button" disabled={disconnected || sendingImage || (!text.trim() && !image)} aria-label="Send message">{sendingImage ? <LoaderCircle className="spin" size={20} /> : <Send size={20} />}</button>
        </form>
      </section>

      {lightbox && <div className="lightbox" role="dialog" aria-modal="true" onMouseDown={(event) => event.target === event.currentTarget && setLightbox(null)}><button className="lightbox-close" onClick={() => setLightbox(null)} aria-label="Close"><X size={24} /></button><img src={lightbox.image_url} alt={lightbox.content || "Shared image"} />{lightbox.content && <p>{lightbox.content}</p>}</div>}
      {forwarding && <ForwardModal message={forwarding} scope="private" onClose={() => setForwarding(null)} onDone={(name, forwardedMessage, targetType, targetId) => { if (targetType === "private" && targetId === contactId) addMessage(forwardedMessage); setForwarding(null); setToast({ kind: "success", message: `Forwarded to ${name}` }); }} />}
      <Toast toast={toast} onClose={() => setToast(null)} />
    </main>
  );
}
