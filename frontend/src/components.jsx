import { CheckCircle2, X, AlertCircle } from "lucide-react";

export function Avatar({ name, src, size = "medium", online = false }) {
  const initials = (name || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  return (
    <span className={`avatar avatar-${size}`} aria-hidden="true">
      {src ? <img src={src} alt="" /> : initials || "?"}
      {online && <i className="avatar-presence" />}
    </span>
  );
}

export function Modal({ title, children, onClose }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <header><div><span className="eyebrow">LAN Chat</span><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button></header>
        {children}
      </section>
    </div>
  );
}

export function Toast({ toast, onClose }) {
  if (!toast) return null;
  return (
    <div className={`toast toast-${toast.kind || "error"}`} role="status">
      {toast.kind === "success" ? <CheckCircle2 size={19} /> : <AlertCircle size={19} />}
      <span>{toast.message}</span>
      <button onClick={onClose} aria-label="Dismiss"><X size={17} /></button>
    </div>
  );
}

export function EmptyState({ icon: Icon, title, detail, action }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={23} /></span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  );
}
