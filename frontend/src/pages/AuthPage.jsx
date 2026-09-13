import { useState } from "react";
import { ArrowRight, Eye, EyeOff, LockKeyhole, MessageCircleMore, UserRound } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export default function AuthPage() {
  const [mode, setMode] = useState("login");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { login, register } = useAuth();
  const navigate = useNavigate();

  const switchMode = (nextMode) => {
    setMode(nextMode);
    setError("");
    setShowPassword(false);
  };

  const submit = async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true);
    setError("");
    try {
      if (mode === "login") await login(values);
      else await register(values);
      navigate("/connections", { replace: true });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-page">
      <div className="auth-glow auth-glow-one" />
      <div className="auth-glow auth-glow-two" />
      <section className="auth-story">
        <div className="brand-lockup">
          <span className="brand-symbol"><MessageCircleMore size={27} strokeWidth={2.2} /></span>
          <span>LAN Chat</span>
        </div>
        <div className="story-copy">
          <span className="eyebrow light">LOCAL · PRIVATE · INSTANT</span>
          <h1>Conversations that stay close to home.</h1>
          <p>Chat instantly with people on your Wi-Fi. No cloud account, no public server—just your local network.</p>
        </div>
        <div className="network-art" aria-hidden="true">
          <span className="network-line line-one" />
          <span className="network-line line-two" />
          <span className="network-node node-one">A</span>
          <span className="network-node node-two">B</span>
          <span className="network-node node-three"><MessageCircleMore size={22} /></span>
        </div>
        <p className="auth-footnote">Designed for trusted local networks</p>
      </section>

      <section className="auth-panel">
        <div className="mobile-brand brand-lockup">
          <span className="brand-symbol"><MessageCircleMore size={24} /></span>
          <span>LAN Chat</span>
        </div>
        <div className="auth-card-react">
          <div className="auth-heading">
            <span className="eyebrow">WELCOME</span>
            <h2>{mode === "login" ? "Good to see you" : "Create your account"}</h2>
            <p>{mode === "login" ? "Log in to continue your conversations." : "Join the conversation in a few seconds."}</p>
          </div>

          <div className="segmented-tabs" role="tablist">
            <button role="tab" aria-selected={mode === "login"} className={mode === "login" ? "active" : ""} onClick={() => switchMode("login")}>Login</button>
            <button role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "active" : ""} onClick={() => switchMode("signup")}>Sign up</button>
          </div>

          <form className="auth-form-react" onSubmit={submit} key={mode}>
            {mode === "signup" && (
              <label className="field-label">
                <span>Display name</span>
                <span className="input-shell"><UserRound size={18} /><input name="name" maxLength="80" placeholder="How people will see you" autoComplete="name" required autoFocus /></span>
              </label>
            )}
            <label className="field-label">
              <span>Username</span>
              <span className="input-shell"><UserRound size={18} /><input name="username" minLength={mode === "signup" ? 3 : 1} maxLength="50" pattern={mode === "signup" ? "[A-Za-z0-9_.-]+" : undefined} placeholder="Enter your username" autoComplete="username" required autoFocus={mode === "login"} /></span>
            </label>
            <label className="field-label">
              <span>Password</span>
              <span className="input-shell"><LockKeyhole size={18} /><input name="password" type={showPassword ? "text" : "password"} minLength={mode === "signup" ? 8 : 1} maxLength="128" placeholder={mode === "signup" ? "At least 8 characters" : "Enter your password"} autoComplete={mode === "signup" ? "new-password" : "current-password"} required /><button type="button" className="field-action" onClick={() => setShowPassword((shown) => !shown)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></span>
            </label>
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="primary-button auth-submit" disabled={busy}>
              <span>{busy ? "Please wait…" : mode === "login" ? "Login" : "Create account"}</span>
              {!busy && <ArrowRight size={19} />}
            </button>
          </form>
          <p className="local-note"><span className="live-dot" /> Connected locally. Your messages stay on this server.</p>
        </div>
      </section>
    </main>
  );
}

