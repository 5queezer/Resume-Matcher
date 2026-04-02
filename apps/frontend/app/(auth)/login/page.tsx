'use client';

import { useEffect, useState } from 'react';
import { LoginForm } from '@/components/auth/login-form';
import { startLogin } from '@/lib/auth/oauth';
import { useTranslations } from '@/lib/i18n/translations';

export default function LoginPage() {
  const { t } = useTranslations();
  const [pkce, setPkce] = useState<{ codeChallenge: string; state: string } | null>(null);
  const [initFailed, setInitFailed] = useState(false);

  useEffect(() => {
    startLogin()
      .then(({ codeChallenge, state }) => {
        setPkce({ codeChallenge, state });
      })
      .catch(() => {
        setInitFailed(true);
      });
  }, []);

  if (initFailed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
        <div className="w-full max-w-sm border border-[#DC2626] bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
          <p className="font-sans text-sm text-[#DC2626]">{t('auth.pkceError')}</p>
        </div>
      </div>
    );
  }

  if (!pkce) return null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <div className="w-full max-w-sm border border-black bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <h1 className="mb-6 font-serif text-2xl font-bold">{t('auth.signIn')}</h1>
        <LoginForm codeChallenge={pkce.codeChallenge} state={pkce.state} />
      </div>
    </div>
  );
}
