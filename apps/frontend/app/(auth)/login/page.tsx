'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { LoginForm } from '@/components/auth/login-form';
import { startLogin, startGoogleLogin } from '@/lib/auth/oauth';
import { apiFetch } from '@/lib/api/client';
import { useTranslations } from '@/lib/i18n/translations';

function LoginContent() {
  const { t } = useTranslations();
  const params = useSearchParams();
  const [pkce, setPkce] = useState<{ codeChallenge: string; state: string } | null>(null);
  const [initFailed, setInitFailed] = useState(false);
  const [providers, setProviders] = useState<string[]>(['credentials']);
  const [googleLoading, setGoogleLoading] = useState(false);

  // Third-party OAuth flow: detect params from authorize redirect
  const oauthClientId = params.get('client_id');
  const oauthRedirectUri = params.get('redirect_uri');
  const oauthCodeChallenge = params.get('code_challenge');
  const oauthCodeChallengeMethod = params.get('code_challenge_method') || 'S256';
  const oauthState = params.get('state');
  const oauthScope = params.get('scope');
  const isThirdPartyOAuth = !!(oauthClientId && oauthRedirectUri && oauthCodeChallenge);

  const errorParam = params.get('error');
  const errorMessage =
    errorParam === 'account_exists'
      ? t('auth.accountExistsPassword')
      : errorParam === 'google_failed' ||
          errorParam === 'invalid_redirect' ||
          errorParam === 'google_not_configured'
        ? t('auth.googleFailed')
        : null;

  useEffect(() => {
    if (isThirdPartyOAuth) {
      // Use OAuth params from URL instead of generating our own PKCE
      setPkce({ codeChallenge: oauthCodeChallenge!, state: oauthState || '' });
    } else {
      startLogin()
        .then(({ codeChallenge, state }) => setPkce({ codeChallenge, state }))
        .catch(() => setInitFailed(true));
    }

    apiFetch('/auth/providers')
      .then((r) => r.json())
      .then((d) => setProviders(d.providers ?? ['credentials']))
      .catch(() => {});
  }, []);

  const handleGoogleLogin = () => {
    setGoogleLoading(true);
    startGoogleLogin();
  };

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

        {errorMessage && (
          <div className="mb-4 border border-[#DC2626] bg-red-50 p-3 font-sans text-sm text-[#DC2626]">
            {errorMessage}
          </div>
        )}

        {providers.includes('google') && (
          <>
            <button
              type="button"
              onClick={handleGoogleLogin}
              disabled={googleLoading}
              className="flex w-full items-center justify-center gap-2 rounded-none border border-black bg-white px-4 py-2 font-sans text-sm hover:bg-gray-50 disabled:opacity-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
                <path
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
                  fill="#4285F4"
                />
                <path
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  fill="#34A853"
                />
                <path
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                  fill="#FBBC05"
                />
                <path
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                  fill="#EA4335"
                />
              </svg>
              {googleLoading ? t('auth.signingIn') : t('auth.signInWithGoogle')}
            </button>
            <div className="relative my-4">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-gray-300" />
              </div>
              <div className="relative flex justify-center">
                <span className="bg-white px-2 font-mono text-xs uppercase tracking-wider text-gray-500">
                  {t('auth.orContinueWith')}
                </span>
              </div>
            </div>
          </>
        )}

        <LoginForm
          codeChallenge={pkce.codeChallenge}
          state={pkce.state}
          oauthClientId={isThirdPartyOAuth ? oauthClientId! : undefined}
          oauthRedirectUri={isThirdPartyOAuth ? oauthRedirectUri! : undefined}
          oauthCodeChallengeMethod={isThirdPartyOAuth ? oauthCodeChallengeMethod : undefined}
          oauthScope={isThirdPartyOAuth ? oauthScope || undefined : undefined}
        />
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
