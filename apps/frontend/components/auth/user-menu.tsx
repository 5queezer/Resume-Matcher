'use client';

import { useAuth } from '@/lib/auth/context';

export function UserMenu() {
  const { user, isLoading, login, logout } = useAuth();

  if (isLoading) return null;

  if (!user) {
    return (
      <button
        onClick={login}
        className="rounded-none border border-black px-3 py-1 font-sans text-sm hover:bg-black hover:text-white"
      >
        Sign in
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3">
      <span className="font-mono text-xs">{user.display_name || user.email}</span>
      <button
        onClick={logout}
        className="rounded-none border border-black px-3 py-1 font-sans text-sm hover:bg-black hover:text-white"
      >
        Sign out
      </button>
    </div>
  );
}
