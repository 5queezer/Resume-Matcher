'use client';

import { useState } from 'react';
import { apiFetch } from '@/lib/api/client';

interface LoginFormProps {
  codeChallenge: string;
  state: string;
}

export function LoginForm({ codeChallenge, state }: LoginFormProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const redirectUri = `${window.location.origin}/callback`;
      const resp = await apiFetch('/oauth/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          email,
          password,
          client_id: 'resume-matcher-web',
          redirect_uri: redirectUri,
          code_challenge: codeChallenge,
          code_challenge_method: 'S256',
          state,
        }),
        redirect: 'manual',
      });

      if (resp.type === 'opaqueredirect' || resp.status === 303) {
        const location = resp.headers.get('location');
        if (location) {
          window.location.href = location;
          return;
        }
      }

      // If we got a redirect response that fetch followed
      if (resp.redirected && resp.url) {
        window.location.href = resp.url;
        return;
      }

      setLoading(false);
      if (resp.status === 401) {
        setError('Invalid email or password');
      } else {
        const data = await resp.json().catch(() => ({}));
        setError(data.detail || 'Login failed');
      }
    } catch {
      setLoading(false);
      setError('Login failed. Please try again.');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="border border-[#DC2626] bg-red-50 p-3 font-sans text-sm text-[#DC2626]">
          {error}
        </div>
      )}
      <div>
        <label htmlFor="email" className="mb-1 block font-mono text-xs uppercase tracking-wider">
          Email
        </label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          className="w-full rounded-none border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black"
        />
      </div>
      <div>
        <label htmlFor="password" className="mb-1 block font-mono text-xs uppercase tracking-wider">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          className="w-full rounded-none border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full rounded-none border border-black bg-black px-4 py-2 font-sans text-sm text-white hover:bg-gray-900 disabled:opacity-50"
      >
        {loading ? 'Signing in...' : 'Sign in'}
      </button>
      <p className="text-center font-sans text-sm text-gray-600">
        No account?{' '}
        <a href="/register" className="text-[#1D4ED8] underline">
          Register
        </a>
      </p>
    </form>
  );
}
