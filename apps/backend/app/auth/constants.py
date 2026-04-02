"""OAuth 2.1 and authentication constants."""

# First-party client (the Next.js SPA)
FIRST_PARTY_CLIENT_ID = "resume-matcher-web"
FIRST_PARTY_REDIRECT_URIS = [
    "http://localhost:3000/callback",
    "http://127.0.0.1:3000/callback",
]

# Token lifetimes
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
AUTHORIZATION_CODE_EXPIRE_MINUTES = 10

# OAuth 2.1 scopes (minimal for now)
SUPPORTED_SCOPES = {"openid", "profile", "email"}
