import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  AtSign,
  CalendarDays,
  Camera,
  Check,
  Copy,
  Fingerprint,
  LoaderCircle,
  MessageCircleMore,
  Save,
  UserCheck,
  UserMinus,
  UserPlus,
  UsersRound,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { copyText } from "../clipboard";
import { Avatar, Modal, Toast } from "../components";
import { useSocket } from "../useSocket";

const MAX_IMAGE_SIZE = 8 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

function joinedLabel(value) {
  if (!value) return "Unknown";
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(new Date(value));
}

export default function ProfilePage() {
  const { publicId } = useParams();
  const { user, updateAvatar, updateProfile } = useAuth();
  const navigate = useNavigate();
  const isSelf = publicId.toUpperCase() === user.public_id;
  const [profile, setProfile] = useState(null);
  const [form, setForm] = useState({ name: "", status_text: "Available", bio: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [toast, setToast] = useState(null);
  const fileRef = useRef(null);

  const loadProfile = useCallback(async () => {
    try {
      const data = await api(`/api/profiles/${encodeURIComponent(publicId)}`);
      setProfile(data.profile);
      setForm({
        name: data.profile.name,
        status_text: data.profile.status_text || "Available",
        bio: data.profile.bio || "",
      });
      document.title = `${data.profile.name} · LAN Chat`;
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setLoading(false);
    }
  }, [publicId]);

  useEffect(() => { loadProfile(); }, [loadProfile]);

  const onSocketEvent = useCallback((event) => {
    if (event.type === "profile_updated" && event.user.public_id === publicId.toUpperCase()) {
      setProfile((current) => current ? { ...current, ...event.user } : current);
      if (!isSelf) return;
      setForm({ name: event.user.name, status_text: event.user.status_text || "Available", bio: event.user.bio || "" });
    }
    if (event.type === "presence" && event.public_id === publicId.toUpperCase()) {
      setProfile((current) => current ? { ...current, online: event.online } : current);
    }
    if (event.type === "connection_removed" && event.public_id === publicId.toUpperCase()) {
      setProfile((current) => current ? { ...current, relationship: "none" } : current);
    }
    if (["connections_changed", "request_answered"].includes(event.type)) loadProfile();
  }, [isSelf, loadProfile, publicId]);
  useSocket(onSocketEvent);

  const saveProfile = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      const updated = await updateProfile({
        name: form.name.trim(),
        status_text: form.status_text.trim() || "Available",
        bio: form.bio.trim(),
      });
      setProfile((current) => ({ ...current, ...updated }));
      setToast({ kind: "success", message: "Profile saved" });
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setSaving(false);
    }
  };

  const changePhoto = async (event) => {
    const image = event.target.files?.[0];
    event.target.value = "";
    if (!image) return;
    if (!ALLOWED_TYPES.has(image.type)) {
      setToast({ message: "Choose a PNG, JPEG, GIF, or WebP image" });
      return;
    }
    if (image.size > MAX_IMAGE_SIZE) {
      setToast({ message: "Profile photos must be 8 MB or smaller" });
      return;
    }
    setUploading(true);
    try {
      const updated = await updateAvatar(image);
      setProfile((current) => ({ ...current, ...updated }));
      setToast({ kind: "success", message: "Profile photo updated" });
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setUploading(false);
    }
  };

  const connect = async () => {
    try {
      await api("/api/requests", { method: "POST", body: JSON.stringify({ public_id: profile.public_id }) });
      setProfile((current) => ({ ...current, relationship: "sent" }));
      setToast({ kind: "success", message: "Connection request sent" });
    } catch (error) {
      setToast({ message: error.message });
    }
  };

  const disconnect = async () => {
    setDisconnecting(true);
    try {
      await api(`/api/connections/${encodeURIComponent(profile.public_id)}`, { method: "DELETE" });
      setProfile((current) => ({ ...current, relationship: "none" }));
      setDisconnectOpen(false);
      setToast({ kind: "success", message: "Disconnected and deleted the private message history" });
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setDisconnecting(false);
    }
  };

  const copyId = async () => {
    try {
      await copyText(profile.public_id);
      setToast({ kind: "success", message: "Public ID copied" });
    } catch {
      setToast({ message: "Copy was blocked by this browser" });
    }
  };

  return (
    <main className="profile-page">
      <header className="profile-topbar">
        <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back"><ArrowLeft size={21} /></button>
        <div className="brand-lockup compact"><span className="brand-symbol"><MessageCircleMore size={21} /></span><span>LAN Chat</span></div>
        <button className="secondary-button profile-home-button" onClick={() => navigate("/connections")}>Connections</button>
      </header>

      {loading ? (
        <div className="profile-loading"><LoaderCircle className="spin" /><span>Loading profile…</span></div>
      ) : profile ? (
        <div className="profile-shell">
          <section className="profile-hero">
            <div className="profile-photo-wrap">
              <Avatar name={profile.name} src={profile.avatar_url} size="profile" online={!isSelf && profile.online} />
              {isSelf && <><input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={changePhoto} hidden /><button className="profile-camera-button" type="button" onClick={() => fileRef.current?.click()} disabled={uploading} aria-label="Change profile photo">{uploading ? <LoaderCircle className="spin" size={18} /> : <Camera size={18} />}</button></>}
            </div>
            <div className="profile-heading">
              <span className={`profile-presence ${profile.online ? "online" : ""}`}>{profile.online ? "Online" : "Offline"}</span>
              <h1>{profile.name}</h1>
              <p>@{profile.username}</p>
              <blockquote>{profile.status_text || "Available"}</blockquote>
            </div>
            {!isSelf && <div className="profile-relationship-actions">
              {profile.relationship === "none" && <button className="primary-button" onClick={connect}><UserPlus size={18} />Connect</button>}
              {profile.relationship === "sent" && <button className="primary-button relationship-disabled" disabled><Check size={18} />Request sent</button>}
              {profile.relationship === "incoming" && <button className="primary-button relationship-disabled" onClick={() => navigate("/connections")}><UserPlus size={18} />View request</button>}
              {profile.relationship === "connected" && <><button className="primary-button relationship-disabled" disabled><UserCheck size={18} />Connected</button><button className="disconnect-button" onClick={() => setDisconnectOpen(true)}><UserMinus size={18} />Disconnect</button></>}
            </div>}
          </section>

          <div className="profile-grid">
            <section className="profile-card profile-about-card">
              <span className="eyebrow">ABOUT</span>
              <h2>{isSelf ? "Customize your profile" : `About ${profile.name.split(" ")[0]}`}</h2>
              {isSelf ? (
                <form className="profile-form" onSubmit={saveProfile}>
                  <label><span>Display name</span><input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} minLength="1" maxLength="80" required /></label>
                  <label><span>Status line</span><input value={form.status_text} onChange={(event) => setForm((current) => ({ ...current, status_text: event.target.value }))} maxLength="80" placeholder="Available" /></label>
                  <label><span>Bio</span><textarea value={form.bio} onChange={(event) => setForm((current) => ({ ...current, bio: event.target.value }))} maxLength="280" rows="5" placeholder="A little about you…" /><small>{form.bio.length}/280</small></label>
                  <button className="primary-button profile-save-button" disabled={saving || !form.name.trim()}>{saving ? <LoaderCircle className="spin" size={18} /> : <Save size={18} />}{saving ? "Saving…" : "Save changes"}</button>
                </form>
              ) : <p className={`profile-bio ${profile.bio ? "" : "empty"}`}>{profile.bio || "No bio added yet."}</p>}
            </section>

            <aside className="profile-card account-card">
              <span className="eyebrow">ACCOUNT INFO</span>
              <div className="profile-detail"><AtSign size={18} /><span><small>Username</small><strong>@{profile.username}</strong></span></div>
              <div className="profile-detail"><Fingerprint size={18} /><span><small>Public ID</small><strong>{profile.public_id}</strong></span><button className="icon-button" onClick={copyId} aria-label="Copy public ID"><Copy size={16} /></button></div>
              <div className="profile-detail"><CalendarDays size={18} /><span><small>Member since</small><strong>{joinedLabel(profile.created_at)}</strong></span></div>
              {!isSelf && <div className="profile-detail"><UsersRound size={18} /><span><small>Groups in common</small><strong>{profile.mutual_group_count}</strong></span></div>}
            </aside>
          </div>
        </div>
      ) : (
        <div className="profile-loading"><span>Profile unavailable</span><button className="secondary-button" onClick={() => navigate("/connections")}>Back to connections</button></div>
      )}

      {disconnectOpen && <Modal title={`Disconnect from ${profile.name}?`} onClose={() => !disconnecting && setDisconnectOpen(false)}><div className="modal-body disconnect-warning"><span className="disconnect-warning-icon"><UserMinus size={23} /></span><p>This removes the connection and permanently deletes all private messages and private images between you. Messages in shared groups will stay.</p><div><button className="secondary-button" disabled={disconnecting} onClick={() => setDisconnectOpen(false)}>Cancel</button><button className="disconnect-confirm-button" disabled={disconnecting} onClick={disconnect}>{disconnecting ? <LoaderCircle className="spin" size={17} /> : <UserMinus size={17} />}{disconnecting ? "Disconnecting…" : "Disconnect"}</button></div></div></Modal>}
      <Toast toast={toast} onClose={() => setToast(null)} />
    </main>
  );
}
