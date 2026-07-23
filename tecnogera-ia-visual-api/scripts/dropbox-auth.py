#!/usr/bin/env python3
"""Obtém um refresh_token do Dropbox via OAuth PKCE em 2 passos.

Útil quando quem tem acesso à conta Dropbox (Tecnogera) não consegue/não
quer rodar scripts. Ele só precisa **clicar num link e copiar um código curto**.

Uso:

    # Passo 1: gera URL de autorização e guarda o code_verifier localmente
    python3 scripts/dropbox-auth.py generate

    # → Envia a URL impressa para quem vai autorizar.
    # → A pessoa abre, autoriza, copia o "code" que o Dropbox mostra.

    # Passo 2: você (com o code da pessoa em mãos) troca por refresh_token
    python3 scripts/dropbox-auth.py exchange <CODE>

    # → Imprime as linhas prontas pra colar no .env.

O ``code_verifier`` é guardado em ``~/.dropbox-auth-verifier`` e nunca sai
da sua máquina. A pessoa que autoriza só vê o ``code_challenge`` (hash) na
URL — sem o verifier, o code é inútil pra terceiros.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERIFIER_FILE = Path.home() / ".dropbox-auth-verifier"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_app_key() -> str:
    key = os.environ.get("DROPBOX_APP_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DROPBOX_APP_KEY="):
                v = line.split("=", 1)[1].strip()
                if v:
                    return v
    raise SystemExit(
        "DROPBOX_APP_KEY não encontrado (defina via env ou no .env do projeto)"
    )


def _cmd_generate() -> int:
    app_key = _load_app_key()
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())

    VERIFIER_FILE.write_text(verifier, encoding="utf-8")
    VERIFIER_FILE.chmod(0o600)

    params = {
        "client_id": app_key,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "token_access_type": "offline",
    }
    auth_url = "https://www.dropbox.com/oauth2/authorize?" + urllib.parse.urlencode(params)

    print()
    print("Envie esta URL para quem tem acesso ao Dropbox da Tecnogera:")
    print()
    print(f"  {auth_url}")
    print()
    print("Instruções para a pessoa autorizar:")
    print("  1. Abrir a URL acima (estar logado na conta Dropbox correta).")
    print("  2. Clicar em 'Allow' / 'Permitir'.")
    print("  3. O Dropbox mostra um código curto na tela. Copiar e enviar de volta.")
    print()
    print(f"Quando receber o code, rode: python3 {sys.argv[0]} exchange <CODE>")
    print()
    return 0


def _cmd_exchange(code: str) -> int:
    if not VERIFIER_FILE.exists():
        raise SystemExit(
            f"verifier não encontrado em {VERIFIER_FILE}. Rode 'generate' primeiro."
        )
    verifier = VERIFIER_FILE.read_text(encoding="utf-8").strip()
    app_key = _load_app_key()

    data = urllib.parse.urlencode(
        {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": app_key,
            "code_verifier": verifier,
        }
    ).encode("ascii")
    req = urllib.request.Request(
        "https://api.dropboxapi.com/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"erro do Dropbox HTTP {exc.code}: {body}") from exc

    refresh = payload.get("refresh_token")
    access = payload.get("access_token")
    if not refresh:
        raise SystemExit(f"resposta sem refresh_token: {payload}")

    # Limpeza imediata: code_verifier não tem mais utilidade.
    VERIFIER_FILE.unlink(missing_ok=True)

    print()
    print("✅ Tokens obtidos. Cole no .env (substituindo valores existentes):")
    print()
    print(f"DROPBOX_APP_KEY={app_key}")
    print(f"DROPBOX_REFRESH_TOKEN={refresh}")
    if access:
        print("# (access_token de curta duração, opcional — refresh basta)")
        print(f"DROPBOX_ACCESS_TOKEN={access}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Dropbox OAuth PKCE em 2 passos.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="Gera code_verifier + URL de autorização.")
    ex = sub.add_parser("exchange", help="Troca code recebido por refresh_token.")
    ex.add_argument("code", help="Code que o Dropbox exibiu para a pessoa autorizadora.")
    args = parser.parse_args()

    if args.cmd == "generate":
        return _cmd_generate()
    if args.cmd == "exchange":
        return _cmd_exchange(args.code)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
