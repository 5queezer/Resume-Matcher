'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { silentRefresh, logout as oauthLogout } from './oauth';
import { apiFetch } from '@/lib/api/client';
import { setTokenGetter } from '@/lib/api/client';

interface User {
  id: string;
  email: string;
  display_name: string | null;
}

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  getToken: () => Promise<string | null>;
  login: () => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const tokenRef = useRef<string | null>(null);
  const expiresAtRef = useRef<number>(0);

  const setToken = useCallback((token: string, expiresIn: number) => {
    tokenRef.current = token;
    expiresAtRef.current = Date.now() + (expiresIn - 60) * 1000;
  }, []);

  const fetchUser = useCallback(async (token: string) => {
    const resp = await apiFetch('/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.ok) {
      setUser(await resp.json());
    } else {
      setUser(null);
    }
  }, []);

  const getToken = useCallback(async (): Promise<string | null> => {
    if (tokenRef.current && Date.now() < expiresAtRef.current) {
      return tokenRef.current;
    }
    const result = await silentRefresh();
    if (result) {
      setToken(result.access_token, result.expires_in);
      return result.access_token;
    }
    tokenRef.current = null;
    setUser(null);
    return null;
  }, [setToken]);

  const login = useCallback(() => {
    window.location.href = '/login';
  }, []);

  const logoutFn = useCallback(async () => {
    await oauthLogout();
    tokenRef.current = null;
    expiresAtRef.current = 0;
    setUser(null);
  }, []);

  useEffect(() => {
    setTokenGetter(getToken);

    (async () => {
      const result = await silentRefresh();
      if (result) {
        setToken(result.access_token, result.expires_in);
        await fetchUser(result.access_token);
      }
      setIsLoading(false);
    })();
  }, [getToken, setToken, fetchUser]);

  return (
    <AuthContext value={{ user, isLoading, getToken, login, logout: logoutFn }}>
      {children}
    </AuthContext>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
