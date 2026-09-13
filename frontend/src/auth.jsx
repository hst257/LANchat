import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/api/me")
      .then((data) => setUser(data.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      async updateAvatar(image) {
        const body = new FormData();
        body.append("image", image);
        const data = await api("/api/me/avatar", { method: "POST", body });
        setUser(data.user);
        return data.user;
      },
      async updateProfile(details) {
        const data = await api("/api/me/profile", {
          method: "PATCH",
          body: JSON.stringify(details),
        });
        setUser(data.user);
        return data.user;
      },
      async login(credentials) {
        const data = await api("/api/login", {
          method: "POST",
          body: JSON.stringify(credentials),
        });
        setUser(data.user);
      },
      async register(details) {
        const data = await api("/api/register", {
          method: "POST",
          body: JSON.stringify(details),
        });
        setUser(data.user);
      },
      async logout() {
        await api("/api/logout", { method: "POST" });
        setUser(null);
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
