'use client';

import { useEffect, useState } from 'react';
import { LoginForm } from '@/components/auth/login-form';
import { startLogin } from '@/lib/auth/oauth';

export default function LoginPage() {
  const [pkce, setPkce] = useState<{ codeChallenge: string; state: string } | null>(null);

  useEffect(() => {
    startLogin().then(({ codeChallenge, state }) => {
      setPkce({ codeChallenge, state });
    });
  }, []);

  if (!pkce) return null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <div className="w-full max-w-sm border border-black bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <h1 className="mb-6 font-serif text-2xl font-bold">Sign In</h1>
        <LoginForm codeChallenge={pkce.codeChallenge} state={pkce.state} />
      </div>
    </div>
  );
}
