import { apiFetch, API_BASE } from '@/lib/api/client';
import { generatePKCE } from './pkce';

const CLIENT_ID = 'resume-matcher-web';

const VERIFIER_KEY = 'oauth_code_verifier';
const STATE_KEY = 'oauth_state';

function getRedirectUri(): string {
  if (typeof window === 'undefined') return 'http://localhost:3000/callback';
  return `${window.location.origin}/callback`;
}

export async function startLogin(): Promise<{
  codeChallenge: string;
  codeVerifier: string;
  state: string;
}> {
  const { codeVerifier, codeChallenge } = await generatePKCE();
  const state = crypto.randomUUID();
  sessionStorage.setItem(VERIFIER_KEY, codeVerifier);
  sessionStorage.setItem(STATE_KEY, state);
  return { codeChallenge, codeVerifier, state };
}

export async function startGoogleLogin(): Promise<void> {
  const { codeVerifier, codeChallenge } = await generatePKCE();
  const state = crypto.randomUUID();
  sessionStorage.setItem(VERIFIER_KEY, codeVerifier);
  sessionStorage.setItem(STATE_KEY, state);

  const params = new URLSearchParams({
    state,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
    redirect_uri: getRedirectUri(),
  });
  window.location.href = `${API_BASE}/oauth/google/start?${params}`;
}

export async function exchangeCode(
  code: string,
  state: string | null
): Promise<{
  access_token: string;
  expires_in: number;
}> {
  const savedState = sessionStorage.getItem(STATE_KEY);
  if (!state || !savedState || state !== savedState) {
    throw new Error('OAuth state mismatch');
  }

  const codeVerifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!codeVerifier) throw new Error('Missing PKCE code_verifier');

  const resp = await apiFetch('/oauth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      grant_type: 'authorization_code',
      code,
      code_verifier: codeVerifier,
      client_id: CLIENT_ID,
      redirect_uri: getRedirectUri(),
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Token exchange failed');
  }

  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);

  return resp.json();
}

export async function silentRefresh(): Promise<{
  access_token: string;
  expires_in: number;
} | null> {
  try {
    const resp = await apiFetch('/oauth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ grant_type: 'refresh_token' }),
    });
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  try {
    const resp = await apiFetch('/oauth/revoke', {
      method: 'POST',
      credentials: 'include',
    });
    if (!resp.ok) {
      console.warn('Revoke request failed:', resp.status);
    }
  } catch {
    // Network error during revoke -- proceed with local cleanup anyway
    console.warn('Revoke request failed');
  }
}
