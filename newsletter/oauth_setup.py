#!/usr/bin/env python3
"""Generate Google OAuth refresh token for Gmail send scope."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> None:
    load_dotenv()

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required in .env")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPE)
    creds = flow.run_local_server(
        host="localhost",
        port=8080,
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )

    out = {
        "refresh_token": creds.refresh_token,
        "scopes": creds.scopes,
    }

    out_path = Path("oauth_token.json")
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Saved refresh token to oauth_token.json")


if __name__ == "__main__":
    main()
