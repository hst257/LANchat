import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Bell, BellOff, Check, ChevronRight, Clock3, Copy, LogOut, MessageCircleMore, Plus, Search, UserRoundPlus, UsersRound, Wifi, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { copyText } from "../clipboard";
import { Avatar, EmptyState, Modal, Toast } from "../components";
import { useSocket } from "../useSocket";

export default function ConnectionsPage() {
  const { user, logout, updateAvatar } = useAuth();
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const uploadPhoto = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) { setToast({ message: "Profile photos must be 8 MB or smaller" }); return; }
    setUploadingPhoto(true);
    try { await updateAvatar(file); setToast({ kind: "success", message: "Profile photo updated" }); }
    catch (e) { setToast({ message: e.message }); }
    finally { setUploadingPhoto(false); }
  };
  const navigate = useNavigate();
  const [connections, setConnections] = useState([]);
  const [groups, setGroups] = useState([]);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchId, setSearchId] = useState("");
  const [searchUsername, setSearchUsername] = useState("");
  const [searchResult, setSearchResult] = useState(undefined);
  const [searching, setSearching] = useState(false);
  const [groupOpen, setGroupOpen] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [selectedMembers, setSelectedMembers] = useState(new Set());
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [toast, setToast] = useState(null);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const notificationRef = useRef(null);

  const loadConnections = useCallback(async () => {
    const data = await api("/api/connections");
    setConnections(data.connections);
  }, []);

  const loadRequests = useCallback(async () => {
    const data = await api("/api/requests/incoming");
    setRequests(data.requests);
  }, []);

  const loadGroups = useCallback(async () => {
    const data = await api("/api/groups");
    setGroups(data.groups);
  }, []);

  const loadAll = useCallback(async () => {
    try {
      await Promise.all([loadConnections(), loadRequests(), loadGroups()]);
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setLoading(false);
    }
  }, [loadConnections, loadRequests, loadGroups]);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => {
    const closeNotifications = (event) => {
      if (!notificationRef.current?.contains(event.target)) setNotificationsOpen(false);
    };
    document.addEventListener("pointerdown", closeNotifications);
    return () => document.removeEventListener("pointerdown", closeNotifications);
  }, []);

  const onSocketEvent = useCallback((event) => {
    if (event.type === "profile_updated") {
      setConnections(items => items.map(item => item.public_id === event.user.public_id ? { ...item, ...event.user } : item));
      setRequests(items => items.map(item => item.sender.public_id === event.user.public_id ? { ...item, sender: event.user } : item));
      setSearchResult(item => item?.public_id === event.user.public_id ? { ...item, ...event.user } : item);
    }
    if (event.type === "presence") {
      setConnections((items) => items.map((contact) => contact.public_id === event.public_id ? { ...contact, online: event.online } : contact));
      setGroups((items) => items.map((group) => {
        if (!group.members.some((member) => member.public_id === event.public_id)) return group;
        const members = group.members.map((member) => member.public_id === event.public_id ? { ...member, online: event.online } : member);
        return { ...group, members, online_count: members.filter((member) => member.online).length };
      }));
    }
    if (["connection_request", "connections_changed", "request_answered", "groups_changed", "group_members_changed"].includes(event.type)) {
      loadAll();
    }
    if (event.type === "connection_removed") {
      setConnections((items) => items.filter((contact) => contact.public_id !== event.public_id));
    }
  }, [loadAll]);
  const { connected } = useSocket(onSocketEvent);

  const onlineCount = useMemo(() => connections.filter((item) => item.online).length, [connections]);

  const findUser = async (event) => {
    event.preventDefault();
    setSearching(true);
    setSearchResult(undefined);
    try {
      const query = searchUsername.trim()
        ? `username=${encodeURIComponent(searchUsername.trim())}`
        : `q=${encodeURIComponent(searchId.trim())}`;
      const data = await api(`/api/users/search?${query}`);
      setSearchResult(data.user || null);
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setSearching(false);
    }
  };

  const connectTo = async (publicId) => {
    try {
      await api("/api/requests", { method: "POST", body: JSON.stringify({ public_id: publicId }) });
      setSearchResult((result) => ({ ...result, relationship: "sent" }));
      setToast({ kind: "success", message: "Connection request sent" });
    } catch (error) {
      setToast({ message: error.message });
    }
  };

  const respond = async (requestId, action) => {
    try {
      await api(`/api/requests/${requestId}/respond`, { method: "POST", body: JSON.stringify({ action }) });
      await loadAll();
      setToast({ kind: "success", message: action === "accept" ? "Connection accepted" : "Request declined" });
    } catch (error) {
      setToast({ message: error.message });
    }
  };

  const copyId = async () => {
    try {
      await copyText(user.public_id);
      setToast({ kind: "success", message: "Public ID copied" });
    } catch {
      setToast({ message: "Copy was blocked. Press and hold the ID to copy it." });
    }
  };

  const toggleMember = (publicId) => {
    setSelectedMembers((current) => {
      const next = new Set(current);
      if (next.has(publicId)) next.delete(publicId);
      else next.add(publicId);
      return next;
    });
  };

  const createGroup = async (event) => {
    event.preventDefault();
    if (!selectedMembers.size) {
      setToast({ message: "Choose at least one member" });
      return;
    }
    setCreatingGroup(true);
    try {
      const data = await api("/api/groups", {
        method: "POST",
        body: JSON.stringify({ name: groupName, member_ids: [...selectedMembers] }),
      });
      setGroupOpen(false);
      setGroupName("");
      setSelectedMembers(new Set());
      await loadGroups();
      navigate(`/group/${data.group.public_id}`);
    } catch (error) {
      setToast({ message: error.message });
    } finally {
      setCreatingGroup(false);
    }
  };

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <main className="dashboard-page">
      <header className="app-header">
        <div className="header-inner">
          <div className="brand-lockup compact"><span className="brand-symbol"><MessageCircleMore size={23} /></span><span>LAN Chat</span></div>
          <div className="header-actions">
            <span className={`server-pill ${connected ? "connected" : ""}`}><Wifi size={15} />{connected ? "Live" : "Reconnecting"}</span>
            <div className="notification-wrap" ref={notificationRef}>
              <button className="icon-button header-icon" aria-label="Notifications" aria-expanded={notificationsOpen} onClick={() => setNotificationsOpen((open) => !open)}><Bell size={19} />{requests.length > 0 && <i>{requests.length}</i>}</button>
              {notificationsOpen && <section className="notification-popover" aria-label="Notifications">
                <header><div><span className="eyebrow">INBOX</span><strong>Notifications</strong></div>{requests.length > 0 && <span>{requests.length} new</span>}</header>
                {requests.length === 0 ? <div className="no-notifications"><span><BellOff size={21} /></span><strong>No notifications</strong><small>You’re all caught up.</small></div> : <div className="notification-items">{requests.map((request) => <article key={request.id}><Avatar name={request.sender.name} src={request.sender.avatar_url} size="small" /><div><strong>{request.sender.name}</strong><small>wants to connect with you</small></div><span><button className="notification-accept" onClick={() => respond(request.id, "accept")} aria-label={`Accept ${request.sender.name}`}><Check size={15} /></button><button className="notification-decline" onClick={() => respond(request.id, "reject")} aria-label={`Decline ${request.sender.name}`}><X size={15} /></button></span></article>)}</div>}
              </section>}
            </div>
            <button className="profile-chip" onClick={() => navigate(`/profile/${user.public_id}`)} title="Customize your profile"><Avatar name={user.name} src={user.avatar_url} size="small" /><span>{user.name}</span></button>
            <button className="icon-button logout-button" onClick={signOut} title="Sign out" aria-label="Sign out"><LogOut size={18} /></button>
          </div>
        </div>
      </header>

      <div className="dashboard-shell">
        <section className="welcome-row">
          <div><span className="eyebrow">YOUR NETWORK</span><h1>Welcome back, {user.name.split(" ")[0]}</h1><p>Start a conversation with someone on your local network.</p></div>
          <div className="welcome-actions"><button className="secondary-button" onClick={() => setGroupOpen(true)}><UsersRound size={18} />New group</button><button className="primary-button" onClick={() => setSearchOpen(true)}><Plus size={19} />Add connection</button></div>
        </section>

        <section className="identity-banner">
          <div className="identity-main"><label className="profile-photo-picker"><Avatar name={user.name} src={user.avatar_url} size="large" online /><span>{uploadingPhoto ? "Uploading…" : "Change photo"}</span><input type="file" aria-label="Upload profile photo" accept="image/png,image/jpeg,image/gif,image/webp" onChange={uploadPhoto} disabled={uploadingPhoto} /></label><div><span className="identity-label">Your public chat ID</span><strong>{user.public_id}</strong><small>Share this ID so others can find you</small></div></div>
          <button className="copy-button" onClick={copyId}><Copy size={17} />Copy ID</button>
          <div className="identity-stats"><span><strong>{connections.length}</strong> connections</span><i /><span><strong>{onlineCount}</strong> online now</span></div>
        </section>

        <div className="dashboard-grid">
          <div className="dashboard-main-stack"><section className="surface connections-surface">
            <header className="surface-header"><div><span className="eyebrow">PEOPLE</span><h2>Connections</h2></div><span className="surface-count">{connections.length}</span></header>
            <div className="connection-list">
              {loading ? Array.from({ length: 3 }, (_, index) => <div className="contact-row skeleton-row" key={index}><span className="skeleton circle" /><span className="skeleton wide" /></div>) : connections.length === 0 ? (
                <EmptyState icon={UsersRound} title="Your circle is empty" detail="Find someone using their public chat ID." action={<button className="text-button" onClick={() => setSearchOpen(true)}>Add your first connection <ArrowRight size={16} /></button>} />
              ) : connections.map((contact) => (
                <button className="contact-row" key={contact.public_id} onClick={() => navigate(`/chat/${contact.public_id}`)}>
                  <Avatar name={contact.name} src={contact.avatar_url} online={contact.online} />
                  <span className="contact-copy"><strong>{contact.name}</strong><small>{contact.online ? "Available now" : `@${contact.username}`}</small></span>
                  <span className={`presence-label ${contact.online ? "online" : ""}`}>{contact.online ? "Online" : "Offline"}</span>
                  <ChevronRight className="row-chevron" size={19} />
                </button>
              ))}
            </div>
          </section>

          <section className="surface groups-surface">
            <header className="surface-header"><div><span className="eyebrow">SHARED CHATS</span><h2>Groups</h2></div><button className="small-add-button" onClick={() => setGroupOpen(true)}><Plus size={15} />Create</button></header>
            <div className="connection-list">
              {loading ? <div className="contact-row skeleton-row"><span className="skeleton circle" /><span className="skeleton wide" /></div> : groups.length === 0 ? (
                <EmptyState icon={UsersRound} title="No groups yet" detail="Bring several connections into one conversation." action={connections.length > 0 ? <button className="text-button" onClick={() => setGroupOpen(true)}>Create a group <ArrowRight size={16} /></button> : null} />
              ) : groups.map((group) => (
                <button className="contact-row" key={group.public_id} onClick={() => navigate(`/group/${group.public_id}`)}>
                  <span className="group-avatar"><UsersRound size={20} /></span>
                  <span className="contact-copy"><strong>{group.name}</strong><small>{group.member_count} members · {group.online_count} online</small></span>
                  <ChevronRight className="row-chevron" size={19} />
                </button>
              ))}
            </div>
          </section></div>

          <aside className="surface requests-surface">
            <header className="surface-header"><div><span className="eyebrow">INBOX</span><h2>Requests</h2></div>{requests.length > 0 && <span className="request-badge">{requests.length} new</span>}</header>
            <div className="request-list">
              {loading ? <div className="request-card"><span className="skeleton circle" /><span className="skeleton wide" /></div> : requests.length === 0 ? (
                <EmptyState icon={Clock3} title="All caught up" detail="New connection requests will appear here." />
              ) : requests.map((request) => (
                <article className="request-card" key={request.id}>
                  <Avatar name={request.sender.name} src={request.sender.avatar_url} />
                  <div className="request-copy"><strong>{request.sender.name}</strong><small>{request.sender.public_id}</small><p>wants to connect with you</p></div>
                  <div className="request-buttons"><button className="accept-button" onClick={() => respond(request.id, "accept")}><Check size={17} />Accept</button><button className="decline-button" onClick={() => respond(request.id, "reject")} aria-label="Decline"><X size={17} /></button></div>
                </article>
              ))}
            </div>
          </aside>
        </div>
      </div>

      {searchOpen && (
        <Modal title="Add a connection" onClose={() => setSearchOpen(false)}>
          <div className="modal-body">
            <p className="modal-intro">Find someone using either their public chat ID or their username.</p>
            <form className="dual-search-form" onSubmit={findUser}>
              <label><span>Public chat ID</span><span className="input-shell"><Search size={18} /><input value={searchId} onChange={(event) => { setSearchId(event.target.value.toUpperCase()); setSearchUsername(""); }} placeholder="CHAT-A1B2C3D4" minLength="3" maxLength="20" autoFocus /></span></label>
              <div className="search-divider"><span>or</span></div>
              <label><span>Username</span><span className="input-shell"><Search size={18} /><input value={searchUsername} onChange={(event) => { setSearchUsername(event.target.value); setSearchId(""); }} placeholder="username" minLength="3" maxLength="50" /></span></label>
              <button className="primary-button" disabled={searching || (!searchId.trim() && !searchUsername.trim())}>{searching ? "Finding…" : "Find user"}</button>
            </form>
            {searchResult === null && <EmptyState icon={UserRoundPlus} title="No user found" detail="Check the ID and make sure you are using the same server." />}
            {searchResult && (
              <article className="search-person"><Avatar name={searchResult.name} src={searchResult.avatar_url} size="large" /><div><strong>{searchResult.name}</strong><small>{searchResult.public_id}</small><span>@{searchResult.username}</span></div><button className="primary-button" disabled={searchResult.relationship !== "none"} onClick={() => connectTo(searchResult.public_id)}>{searchResult.relationship === "none" ? "Connect" : searchResult.relationship === "sent" ? "Request sent" : searchResult.relationship === "connected" ? "Connected" : "Check requests"}</button></article>
            )}
          </div>
        </Modal>
      )}
      {groupOpen && (
        <Modal title="Create a group" onClose={() => setGroupOpen(false)}>
          <form className="modal-body group-form" onSubmit={createGroup}>
            <label className="field-label"><span>Group name</span><span className="input-shell"><UsersRound size={18} /><input value={groupName} onChange={(event) => setGroupName(event.target.value)} placeholder="Weekend plans" minLength="1" maxLength="100" autoFocus required /></span></label>
            <div className="member-picker-header"><div><strong>Select members</strong><small>Choose from your connections</small></div><span>{selectedMembers.size} selected</span></div>
            <div className="member-picker">
              {connections.length === 0 ? <EmptyState icon={UsersRound} title="No connections available" detail="Connect with someone before creating a group." /> : connections.map((contact) => (
                <label className={`member-option ${selectedMembers.has(contact.public_id) ? "selected" : ""}`} key={contact.public_id}>
                  <input type="checkbox" checked={selectedMembers.has(contact.public_id)} onChange={() => toggleMember(contact.public_id)} />
                  <Avatar name={contact.name} src={contact.avatar_url} online={contact.online} />
                  <span><strong>{contact.name}</strong><small>@{contact.username}</small></span>
                  <i>{selectedMembers.has(contact.public_id) && <Check size={14} />}</i>
                </label>
              ))}
            </div>
            <button className="primary-button group-create-button" disabled={creatingGroup || !groupName.trim() || !selectedMembers.size}>{creatingGroup ? "Creating…" : `Create group${selectedMembers.size ? ` with ${selectedMembers.size + 1}` : ""}`}</button>
          </form>
        </Modal>
      )}
      <Toast toast={toast} onClose={() => setToast(null)} />
    </main>
  );
}
