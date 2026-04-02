'use client';

import { Suspense, useEffect, useRef } from 'react';
import { useSearchParams } from 'next/navigation';
import { exchangeCode } from '@/lib/auth/oauth';

function CallbackHandler() {
  const params = useSearchParams();
  const exchanged = useRef(false);

  useEffect(() => {
    if (exchanged.current) return;
    exchanged.current = true;

    const code = params.get('code');
    const state = params.get('state');
    const savedState = sessionStorage.getItem('oauth_state');

    if (!code) {
      window.location.href = '/login';
      return;
    }

    if (state && savedState && state !== savedState) {
      window.location.href = '/login';
      return;
    }

    exchangeCode(code)
      .then(() => {
        window.location.href = '/';
      })
      .catch(() => {
        window.location.href = '/login';
      });
  }, [params]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <p className="font-sans text-sm text-gray-600">Completing sign in...</p>
    </div>
  );
}

export default function CallbackPage() {
  return (
    <Suspense>
      <CallbackHandler />
    </Suspense>
  );
}
