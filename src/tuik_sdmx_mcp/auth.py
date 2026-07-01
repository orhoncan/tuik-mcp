"""TÜİK SDMX kimlik doğrulama - Keycloak access token yönetimi.

TÜİK, SDMX servislerini (nsiws.tuik.gov.tr) TÜİK giriş sistemi üzerinden
alınan kısa ömürlü (varsayılan 300 sn) Bearer token ile korur. Kullanıcı,
Veri Portalı'nda SMS doğrulaması yaparak bir API Key üretir; bu key ile
token servisinden access_token alınır ve servis çağrılarında
`Authorization: Bearer <token>` başlığında gönderilir.

API Key ortam değişkeninden okunur: TUIK_API_KEY
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
CLIENT_ID = "nsi-ws-consumer"
API_KEY_ENV = "TUIK_API_KEY"

# Token süresi dolmadan bu kadar saniye önce yenile (saat kayması + istek
# gecikmesine karşı güvenlik payı).
_REFRESH_MARGIN = 30.0
_DEFAULT_EXPIRES_IN = 300


def config_path() -> Path:
    """Kaydedilen API anahtarının tutulduğu dosya yolu.

    XDG_CONFIG_HOME varsa onu, yoksa ~/.config'i kullanır.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(base) / "tuik-sdmx-mcp" / "config.json"


def load_saved_key() -> str:
    """Config dosyasından kaydedilmiş API anahtarını okur (yoksa boş string)."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return (data.get("api_key") or "").strip()
    except (FileNotFoundError, ValueError, OSError):
        return ""


def save_key(api_key: str) -> Path:
    """API anahtarını config dosyasına atomik ve 0600 izinle kaydeder.

    Önce aynı dizinde 0600 izinli geçici dosyaya yazıp fsync eder, sonra
    os.replace ile atomik olarak yerine koyar. Böylece hiçbir an dosya dünyaya
    açık kalmaz ve eşzamanlı/yarım yazımlar bozuk JSON bırakmaz.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"api_key": api_key.strip()}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()  # os.replace başarılıysa zaten yok; hata olduysa temizle
        except OSError:
            pass
    return path


class AuthError(RuntimeError):
    """SDMX kimlik doğrulama katmanındaki hatalar için temel sınıf."""


class MissingAPIKeyError(AuthError):
    """Hiçbir kaynakta API anahtarı tanımlı değil (kullanıcının anahtar vermesi gerek)."""


class TokenServiceError(AuthError):
    """Anahtar var ama token servisi hata/beklenmedik yanıt döndürdü (geçici olabilir)."""


class TokenManager:
    """Keycloak access token'ını alır, cache'ler ve süresi dolunca yeniler.

    Aynı process içinde tek örnek (singleton) olarak kullanılması beklenir;
    eşzamanlı çağrılarda tek token yenileme yapılması için asyncio.Lock kullanır.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._explicit_key = api_key
        self._token: str = ""
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def api_key(self) -> str:
        # Öncelik sırası: açık verilen anahtar > ortam değişkeni > kayıtlı
        # config dosyası. Her erişimde okunur ki anahtar döndürüldüğünde ya da
        # kullanıcı sonradan ayarladığında yeniden başlatmaya gerek kalmasın.
        return (
            self._explicit_key
            or os.environ.get(API_KEY_ENV, "")
            or load_saved_key()
        )

    def _valid(self) -> bool:
        return bool(self._token) and time.monotonic() < self._expires_at

    async def token(self, client: httpx.AsyncClient) -> str:
        """Geçerli bir access token döndürür; gerekiyorsa yeniler."""
        if self._valid():
            return self._token
        async with self._lock:
            # Lock beklerken başka bir çağrı yenilemiş olabilir.
            if self._valid():
                return self._token
            await self._refresh(client)
            return self._token

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        key = self.api_key
        if not key:
            raise MissingAPIKeyError(
                f"{API_KEY_ENV} ortam değişkeni tanımlı değil. TÜİK Veri "
                "Portalı'ndan (SMS doğrulamalı) bir API Key üretip "
                f"{API_KEY_ENV} olarak ayarlayın."
            )
        resp = await client.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "api_key": key,
            },
            timeout=30.0,
        )
        # Token servisi geçersiz anahtarda 400/401 döndürebildiği gibi 500 de
        # döndürebiliyor. Anahtar var olduğu için bunlar "anahtar eksik" değil,
        # servis/anahtar-geçerlilik hatasıdır.
        if resp.status_code != 200:
            raise TokenServiceError(
                f"Token alınamadı (HTTP {resp.status_code}). API anahtarı "
                "geçersiz/süresi dolmuş olabilir ya da TÜİK giriş servisi "
                "geçici olarak yanıt vermiyor olabilir. Anahtarı Veri "
                "Portalı'ndan yeniden üretmeyi deneyin."
            )
        try:
            payload = resp.json()
        except ValueError:
            raise TokenServiceError(
                "Token servisi geçerli bir JSON yanıtı döndürmedi (geçici bir "
                "sorun olabilir, birazdan tekrar deneyin)."
            )
        token = payload.get("access_token")
        if not token:
            raise TokenServiceError(
                "Token servisi beklenen access_token'ı döndürmedi."
            )
        self._token = token
        expires_in = payload.get("expires_in", _DEFAULT_EXPIRES_IN)
        self._expires_at = time.monotonic() + max(0.0, expires_in - _REFRESH_MARGIN)

    async def adopt(self, token: str, expires_at: float) -> None:
        """Başka bir yerde alınmış geçerli token'ı lock altında benimser."""
        async with self._lock:
            self._token = token
            self._expires_at = expires_at

    def reset(self) -> None:
        """Cache'lenen token'ı temizler (testler ve manuel yenileme için)."""
        self._token = ""
        self._expires_at = 0.0


# Process başına tek token yöneticisi.
_manager = TokenManager()


async def auth_headers(
    client: httpx.AsyncClient,
    accept: str = "application/json",
) -> dict[str, str]:
    """SDMX servis çağrıları için Authorization + Accept başlıklarını üretir."""
    token = await _manager.token(client)
    return {"Authorization": f"Bearer {token}", "Accept": accept}


def has_api_key() -> bool:
    """Herhangi bir kaynakta (açık/env/kayıtlı) API anahtarı tanımlı mı?"""
    return bool(_manager.api_key)


async def validate_and_save_key(
    client: httpx.AsyncClient, api_key: str
) -> Path:
    """API anahtarını token servisiyle doğrular ve geçerliyse kaydeder.

    Anahtar boşsa ValueError, token servisi reddederse MissingAPIKeyError
    yükseltir. Başarılıysa anahtarı config dosyasına yazar, token cache'ini
    doğrulanmış token'la doldurur ve dosya yolunu döndürür.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("API anahtarı boş olamaz.")
    # Doğrula: geçici bir yönetici ile token almayı dene.
    probe = TokenManager(api_key=api_key)
    await probe._refresh(client)  # hata -> TokenServiceError
    path = save_key(api_key)
    # Doğrulamada alınan token'ı kalıcı yöneticiye (lock altında) taşı; böylece
    # ikinci bir (kırılgan olabilen) token isteğine gerek kalmaz.
    await _manager.adopt(probe._token, probe._expires_at)
    return path
