import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Forward, Pencil, Reply, Trash2, X } from "lucide-react";
import { api } from "./api";
import { useAuth } from "./auth";
import { EmojiPicker } from "./ChatFeatures";

export default function MessageActions({ message, scope, canModify, onUpdate, onReply, onForward }) {
  const { user } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mode, setMode] = useState(null);
  const [value, setValue] = useState(message.content);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wrapRef = useRef(null);

  useEffect(() => {
    const closeMenu = (event) => {
      if (!wrapRef.current?.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeMenu);
    return () => document.removeEventListener("pointerdown", closeMenu);
  }, []);

  if (message.kind === "deleted") return null;

  const chooseMode = (nextMode) => {
    setMenuOpen(false);
    setError("");
    setValue(message.content || "");
    setMode(nextMode);
  };

  const closePanel = () => {
    setMode(null);
    setError("");
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await api(`/api/message-actions/${scope}/${message.id}`, {
        method: mode === "delete" ? "DELETE" : "PATCH",
        ...(mode === "edit" ? { body: JSON.stringify({ content: value }) } : {}),
      });
      onUpdate(data.message);
      closePanel();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  const react = async (emoji) => {
    setError("");
    try {
      const data = await api(`/api/message-actions/${scope}/${message.id}/reactions`, {
        method: "POST",
        body: JSON.stringify({ emoji }),
      });
      onUpdate(data.message);
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  return (
    <div className={`message-action-wrap ${menuOpen || mode ? "open" : ""}`} ref={wrapRef}>
      <button type="button" className="quick-reply-button" onClick={() => onReply(message)} aria-label="Reply to message" title="Reply"><Reply size={16} /></button>
      <button
        type="button"
        className="message-menu-trigger"
        aria-label="Message options"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        <ChevronDown size={17} />
      </button>

      {menuOpen && (
        <div className="message-menu-popover" role="menu">
          <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onReply(message); }}><Reply size={15} /> Reply</button>
          <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onForward(message); }}><Forward size={15} /> Forward</button>
          {canModify && <><span className="message-menu-divider" /><button type="button" role="menuitem" onClick={() => chooseMode("edit")}><Pencil size={15} /> Edit{message.kind === "image" ? " caption" : ""}</button><button type="button" role="menuitem" className="danger" onClick={() => chooseMode("delete")}><Trash2 size={15} /> Delete</button></>}
        </div>
      )}

      <EmojiPicker reactions className="message-reaction-picker" onSelect={react} />

      {message.reactions?.length > 0 && <div className="message-reactions">{message.reactions.map((reaction) => <button type="button" key={reaction.emoji} className={reaction.user_ids.includes(user.public_id) ? "reacted" : ""} onClick={() => react(reaction.emoji)} title={reaction.names.join(", ")}><span>{reaction.emoji}</span><small>{reaction.count}</small></button>)}</div>}

      {mode && (
        <div className={`message-action-panel ${mode}`}>
          {mode === "edit" ? (
            <>
              <label htmlFor={`edit-message-${message.id}`}>Edit message</label>
              <textarea
                id={`edit-message-${message.id}`}
                aria-label="Edit message"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                maxLength={message.kind === "image" ? 1000 : 4000}
                autoFocus
              />
            </>
          ) : (
            <><strong>Delete this message?</strong><span>It will be removed for everyone.</span></>
          )}
          {error && <span className="message-action-error" role="alert">{error}</span>}
          <div className="message-action-buttons">
            <button type="button" className="action-cancel" disabled={busy} onClick={closePanel}><X size={15} /> Cancel</button>
            <button type="button" className={mode === "delete" ? "action-delete" : "action-save"} disabled={busy || (mode === "edit" && !value.trim())} onClick={save}>
              {mode === "delete" ? <Trash2 size={15} /> : <Check size={15} />}
              {busy ? "Working…" : mode === "delete" ? "Delete" : "Save"}
            </button>
          </div>
        </div>
      )}
      {!mode && error && <span className="message-action-error floating" role="alert">{error}</span>}
    </div>
  );
}
