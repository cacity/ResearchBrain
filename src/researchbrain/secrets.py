from __future__ import annotations

import os

import keyring

SERVICE_NAME = "ResearchBrain"
ENV_NAMES = {
    "minimax_api_key": "MINIMAX_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "ncbi_api_key": "NCBI_API_KEY",
    "openalex_api_key": "OPENALEX_API_KEY",
}


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    def get(self, name: str) -> str:
        environment_name = ENV_NAMES.get(name)
        if environment_name and os.getenv(environment_name):
            return str(os.getenv(environment_name))
        try:
            return keyring.get_password(SERVICE_NAME, name) or ""
        except keyring.errors.KeyringError:
            return ""

    def set(self, name: str, value: str) -> None:
        if name not in ENV_NAMES:
            raise ValueError("unsupported secret name")
        try:
            if value:
                keyring.set_password(SERVICE_NAME, name, value)
            else:
                keyring.delete_password(SERVICE_NAME, name)
        except keyring.errors.PasswordDeleteError:
            if value:
                raise
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(str(exc)) from exc

    def status(self) -> dict[str, bool]:
        return {name: bool(self.get(name)) for name in ENV_NAMES}
