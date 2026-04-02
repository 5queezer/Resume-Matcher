'use client';

import { useState } from 'react';
import { apiFetch } from '@/lib/api/client';

export function RegisterForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const resp = await apiFetch('/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          password,
          display_name: displayName || undefined,
        }),
      });

      if (resp.status === 201) {
        window.location.href = '/login';
        return;
      }

      setLoading(false);
      const data = await resp.json().catch(() => ({}));
      if (resp.status === 409) {
        setError('An account with this email already exists');
      } else {
        setError(data.detail || 'Registration failed');
      }
    } catch {
      setLoading(false);
      setError('Registration failed. Please try again.');
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
        <label
          htmlFor="displayName"
          className="mb-1 block font-mono text-xs uppercase tracking-wider"
        >
          Name (optional)
        </label>
        <input
          id="displayName"
          type="text"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="w-full rounded-none border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black"
        />
      </div>
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
        <label
          htmlFor="password"
          className="mb-1 block font-mono text-xs uppercase tracking-wider"
        >
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
        <p className="mt-1 font-mono text-xs text-gray-500">Minimum 8 characters</p>
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full rounded-none border border-black bg-black px-4 py-2 font-sans text-sm text-white hover:bg-gray-900 disabled:opacity-50"
      >
        {loading ? 'Creating account...' : 'Create account'}
      </button>
      <p className="text-center font-sans text-sm text-gray-600">
        Already have an account?{' '}
        <a href="/login" className="text-[#1D4ED8] underline">
          Sign in
        </a>
      </p>
    </form>
  );
}
