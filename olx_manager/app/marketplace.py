import json
import os
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Protocol
from urllib.parse import urlencode, urlsplit

import httpx
import simplejson
from cryptography.fernet import Fernet, InvalidToken

from media import DomainError
from security import equal, mac
from store import now, packed

API = "https://www.olx.pl/api/partner"
TOKEN = "https://www.olx.pl/api/open/oauth/token"
AUTH = "https://www.olx.pl/oauth/authorize"
PHOTO_TTL = 6 * 3600


@dataclass(frozen=True)
class PublicationResult:
    status: str
    provider_id: str | None = None
    provider_status: str | None = None
    url: str | None = None


class MarketplaceAdapter(Protocol):
    def submit(self, attempt, draft, token, payload) -> PublicationResult: ...


class MockMarketplaceAdapter:
    def submit(self, attempt, draft, token=None, payload=None):
        return PublicationResult("mock_published", attempt["id"], "mock_only", "#demo/" + draft["id"])


def provider_error(code, message, status=409):
    return DomainError(code, message, status)


def advert_result(data):
    if not isinstance(data, dict) or type(data.get("id")) is not int or data["id"] <= 0:
        raise provider_error("OLX_RESPONSE", "OLX nie zwrócił potwierdzonego identyfikatora.")
    state = data.get("status")
    if (
        not isinstance(state, str)
        or not state.isascii()
        or not state.replace("_", "").isalpha()
        or len(state) > 40
    ):
        state = "unknown"
    url = data.get("url")
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
        if not parts or parts.scheme != "https" or parts.netloc not in {"www.olx.pl", "olx.pl"}:
            url = None
    except ValueError:
        url = None
    status = {
        "active": "published",
        "new": "submitted",
        "limited": "payment_required",
        "unpaid": "payment_required",
        "removed_by_user": "inactive",
        "finished": "inactive",
        "outdated": "inactive",
        "removed_by_moderator": "inactive",
    }.get(state, "submitted")
    return PublicationResult(status, str(data["id"]), state, url if status == "published" else None)


class OlxMarketplaceAdapter:
    def __init__(self, store, settings, client=None):
        self.store = store
        self.settings = settings
        self.http = client or httpx.Client(
            timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False
        )
        self.lock = RLock()
        self.pending = None
        key_path = store.data_dir / "olx.key"
        if not key_path.exists():
            if store.load_account() is not None:
                raise RuntimeError("Brakuje klucza /data/olx.key z kopii zapasowej.")
            key = Fernet.generate_key()
            with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as target:
                target.write(key)
        self.key = key_path.read_bytes()
        self.fernet = Fernet(self.key)

    def _load(self):
        encrypted = self.store.load_account()
        if encrypted is None:
            return None
        try:
            return json.loads(self.fernet.decrypt(encrypted))
        except (InvalidToken, ValueError) as err:
            raise provider_error(
                "OLX_VAULT", "Nie można odczytać sejfu OLX. Przywróć pasujący klucz."
            ) from err

    def _save(self, account):
        self.store.save_account(self.fernet.encrypt(packed(account).encode()))

    def status(self):
        account = self._load()
        return {
            "platform": "olx",
            "status": "OLX_ACCOUNT_LINKED" if account else "OLX_NOT_CONFIGURED",
            "write_enabled": self.settings.olx_write_enabled,
            "allowed_categories": sorted(self.settings.allowed_categories),
            "no_charge_confirmed": self.settings.olx_no_charge_confirmed,
            "redirect_uri": self.settings.redirect_uri if self.settings.olx_public_base else None,
        }

    def start_oauth(self, client_id, client_secret):
        if not self.settings.olx_public_base:
            raise provider_error(
                "OLX_REDIRECT_REQUIRED", "Ustaw opcję olx_public_base i zarejestruj adres callback w OLX."
            )
        with self.lock:
            state = secrets.token_urlsafe(32)
            self.pending = (state, now() + 600, client_id, client_secret)
            return (
                AUTH
                + "?"
                + urlencode(
                    {
                        "client_id": client_id,
                        "response_type": "code",
                        "state": state,
                        "scope": "v2 read write",
                        "redirect_uri": self.settings.redirect_uri,
                    }
                )
            )

    def callback(self, state, code):
        with self.lock:
            pending = self.pending
            if not pending or pending[1] <= now() or not equal(state, pending[0]):
                raise provider_error("OLX_OAUTH_STATE", "Sesja autoryzacji wygasła lub jest niewłaściwa.")
            self.pending = None
            if not code:
                raise provider_error("OLX_OAUTH_DENIED", "OLX nie zwrócił kodu autoryzacji.")
            account = {"client_id": pending[2], "client_secret": pending[3]}
            account.update(
                self._token(
                    account,
                    grant_type="authorization_code",
                    code=code,
                    scope="v2 read write",
                    redirect_uri=self.settings.redirect_uri,
                )
            )
            profile = self._api("GET", "/users/me", account["access_token"])
            if not isinstance(profile, dict) or type(profile.get("id")) is not int or profile["id"] <= 0:
                raise provider_error("OLX_PROFILE", "Nie można potwierdzić konta OLX.")
            account["user_id"] = str(profile["id"])
            self._save(account)

    def disconnect(self):
        with self.lock:
            self.pending = None
            self.store.clear_account()

    def _request(self, method, url, **kwargs):
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.HTTPError as err:
            raise provider_error(
                "OLX_NETWORK", "Utracono odpowiedź OLX lub połączenie wygasło.", 503
            ) from err
        if not response.is_success:
            labels = {401: "OLX_AUTH", 403: "OLX_FORBIDDEN", 404: "OLX_NOT_FOUND", 429: "OLX_RATE_LIMIT"}
            code = labels.get(
                response.status_code, "OLX_RESPONSE" if response.status_code >= 500 else "OLX_REJECTED"
            )
            raise provider_error(
                code, f"OLX zwrócił HTTP {response.status_code}.", 503 if response.status_code >= 500 else 409
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as err:
            raise provider_error("OLX_RESPONSE", "OLX zwrócił nieczytelną odpowiedź.") from err

    def _token(self, account, **grant):
        data = self._request(
            "POST",
            TOKEN,
            json={
                "client_id": account["client_id"],
                "client_secret": account["client_secret"],
                **grant,
            },
        )
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("access_token"), str)
            or type(data.get("expires_in")) is not int
            or data["expires_in"] <= 0
        ):
            raise provider_error("OLX_TOKEN", "Nie udało się otrzymać kompletnego tokenu OLX.")
        refresh = data.get("refresh_token", account.get("refresh_token"))
        if not isinstance(refresh, str) or not refresh:
            raise provider_error("OLX_TOKEN", "OLX nie zwrócił tokenu odświeżania konta.")
        return {
            "access_token": data["access_token"],
            "refresh_token": refresh,
            "expiry": now() + data["expires_in"],
        }

    def account(self):
        with self.lock:
            account = self._load()
            if not account:
                raise provider_error("OLX_NOT_CONFIGURED", "Połącz zatwierdzone konto przez OLX Partner API.")
            if account["expiry"] <= now() + 60:
                account.update(
                    self._token(account, grant_type="refresh_token", refresh_token=account["refresh_token"])
                )
                self._save(account)
            return account

    def _api(self, method, path, token, **kwargs):
        headers = {"Authorization": "Bearer " + token}
        if "json" in kwargs:
            kwargs["content"] = simplejson.dumps(
                kwargs.pop("json"), use_decimal=True, ensure_ascii=False
            ).encode()
            headers["Content-Type"] = "application/json"
        data = self._request(method, API + path, headers=headers, **kwargs)
        return data.get("data", data) if isinstance(data, dict) else data

    def categories(self, category_id=None):
        token = self.account()["access_token"]
        path = f"/categories/{category_id}" if category_id else "/categories"
        result = self._api("GET", path, token)
        if category_id:
            return {"category": result, "attributes": self._api("GET", path + "/attributes", token)}
        return result

    def city(self, city_id):
        token = self.account()["access_token"]
        path = f"/cities/{city_id}"
        return {
            "city": self._api("GET", path, token),
            "districts": self._api("GET", path + "/districts", token),
        }

    def write_account(self):
        if not self._load():
            raise provider_error("OLX_NOT_CONFIGURED", "Zatwierdzone, oczekuje na połączenie OLX.")
        if not self.settings.olx_write_enabled:
            raise provider_error("OLX_WRITE_DISABLED", "Zapis do OLX jest wyłączony.")
        account = self.account()
        token = account["access_token"]
        user = self._api("GET", "/users/me", token)
        if (
            not isinstance(user, dict)
            or str(user.get("id")) != account["user_id"]
            or user.get("is_business") is not False
            or user.get("status") not in {"confirmed", "active"}
        ):
            raise provider_error("OLX_ACCOUNT_UNVERIFIED", "Wymagane jest zweryfikowane konto prywatne OLX.")
        return account

    @staticmethod
    def _validate_value(value, rule, label):
        if value is None or value == "":
            if rule.get("required") is not False:
                raise provider_error("OLX_ATTRIBUTES", f"Uzupełnij wymagany parametr OLX: {label}.")
            return
        if rule.get("numeric") is True:
            try:
                number = Decimal(value)
                minimum, maximum = rule.get("min"), rule.get("max")
                if (
                    not number.is_finite()
                    or (minimum is not None and number < Decimal(str(minimum)))
                    or (maximum is not None and number > Decimal(str(maximum)))
                ):
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError) as err:
                raise provider_error(
                    "OLX_ATTRIBUTES", f"Sprawdź zakres liczbowy parametru: {label}."
                ) from err

    def prepare(self, draft, account):
        fields = draft["fields"]
        token = account["access_token"]
        category = fields["olx_category_id"]
        if category not in self.settings.allowed_categories:
            raise provider_error("OLX_CATEGORY_CLOSED", "Kategoria OLX nie jest na zaakceptowanej liście.")
        if not self.settings.olx_no_charge_confirmed:
            raise provider_error(
                "OLX_FEES_UNVERIFIED", "Najpierw potwierdź z OLX brak kosztów i zużycia pakietów."
            )
        if not self.settings.olx_public_base:
            raise provider_error(
                "OLX_IMAGES_UNAVAILABLE", "Wymagany jest adres HTTPS z ograniczonym dostępem do zdjęć."
            )
        if fields["delivery"] != "pickup":
            raise provider_error(
                "OLX_DELIVERY_UNSUPPORTED", "Wysyłka jest zapisywana w szkicu. Zapis do OLX obsługuje odbiór."
            )
        city, district = fields["olx_city_id"], fields["olx_district_id"]
        if not city or not fields["olx_contact_name"]:
            raise provider_error("OLX_FIELDS", "Podaj ID miejscowości i nazwę kontaktu OLX.")
        category_path = f"/categories/{category}"
        info = self._api("GET", category_path, token)
        if (
            not isinstance(info, dict)
            or info.get("id") != category
            or info.get("is_leaf") is not True
            or type(info.get("photos_limit")) is not int
            or not 1 <= len(draft["media"]) <= info["photos_limit"]
        ):
            raise provider_error(
                "OLX_CATEGORY_CLOSED", "Niezweryfikowana kategoria końcowa lub limit zdjęć OLX."
            )
        city_info = self._api("GET", f"/cities/{city}", token)
        if (
            not isinstance(city_info, dict)
            or city_info.get("id") != city
            or str(city_info.get("name", "")).casefold() != fields["location"].casefold()
        ):
            raise provider_error(
                "OLX_LOCATION", "Miejscowość musi być zgodna z jej nazwą i identyfikatorem z OLX."
            )
        districts = self._api("GET", f"/cities/{city}/districts", token)
        if (
            not isinstance(districts, list)
            or bool(districts) != bool(district)
            or (
                district
                and not any(isinstance(item, dict) and item.get("id") == district for item in districts)
            )
        ):
            raise provider_error("OLX_LOCATION", "Podaj prawidłową dzielnicę, jeśli miejscowość jej wymaga.")
        rules = self._api("GET", category_path + "/attributes", token)
        if not isinstance(rules, list):
            raise provider_error("OLX_ATTRIBUTES", "Nie udało się potwierdzić parametrów kategorii.")
        price = Decimal(fields["price_minor"]) / 100
        attrs = fields["olx_attributes"]
        encoded = []
        for item in rules:
            if not isinstance(item, dict) or not item.get("code"):
                raise provider_error("OLX_ATTRIBUTES", "OLX zwrócił nieznany schemat kategorii.")
            code = item["code"]
            rule = item.get("validation")
            if not isinstance(rule, dict):
                raise provider_error("OLX_ATTRIBUTES", "OLX zwrócił nieznane reguły atrybutu.")
            kind = rule.get("type")
            if kind == "package" and code == "delivery":
                if rule.get("required") is not False:
                    raise provider_error("OLX_DELIVERY_UNSUPPORTED", "Kategoria wymaga przesyłki OLX.")
                continue
            if kind == "price":
                self._validate_value(price, rule, code)
                continue
            if kind not in {"attribute", "salary"} or (
                kind == "salary" and rule.get("required") is not False
            ):
                raise provider_error(
                    "OLX_ATTRIBUTES", "Ta kategoria wymaga danych nieobsługiwanych w tej wersji."
                )
            if kind == "salary":
                continue
            value = attrs.get(code)
            self._validate_value(value, rule, code)
            if value:
                values = item.get("values") or []
                if values and not any(v.get("code") == value for v in values if isinstance(v, dict)):
                    raise provider_error("OLX_ATTRIBUTES", f"Nieprawidłowa wartość parametru: {code}.")
                encoded.append(
                    {"code": code, "values": [value]}
                    if rule.get("allow_multiple_values")
                    else {"code": code, "value": value}
                )
        if set(attrs) != {item["code"] for item in encoded}:
            raise provider_error("OLX_ATTRIBUTES", "Usuń nieznane lub puste parametry OLX.")
        # Local descriptive attributes and condition are included in the public
        # description as explicitly displayed in the panel preview.
        description = public_description(fields)
        return {
            "title": fields["title"],
            "description": description,
            "category_id": category,
            "advertiser_type": "private",
            "contact": {"name": fields["olx_contact_name"]},
            "location": {"city_id": city, **({"district_id": district} if district else {})},
            "price": {"value": price, "currency": fields["currency"], "negotiable": fields["negotiable"]},
            "attributes": encoded,
        }

    def photo_url(self, attempt_id, asset_id, expires):
        signature = mac(self.key, "olx-image-v1", attempt_id, asset_id, expires)
        return (
            self.settings.olx_public_base.rstrip("/")
            + f"/olx/media/{attempt_id}/{asset_id}"
            + "?"
            + urlencode({"exp": expires, "sig": signature})
        )

    def check_photo_signature(self, attempt_id, asset_id, expires, signature):
        return now() <= expires <= now() + PHOTO_TTL and equal(
            signature, mac(self.key, "olx-image-v1", attempt_id, asset_id, expires)
        )

    def submit(self, attempt, draft, token, payload):
        external_id = attempt["draft_id"]
        expires = attempt["created_at"] + PHOTO_TTL
        photos = [{"url": self.photo_url(attempt["id"], x["id"], expires)} for x in draft["media"]]
        payload = {**payload, "external_id": external_id, "images": photos, "auto_extend_enabled": False}
        if attempt["operation"] == "create":
            return advert_result(self._api("POST", "/adverts", token, json=payload))
        provider_id = attempt["provider_id"]
        if attempt["operation"] == "update":
            result = advert_result(self._api("PUT", f"/adverts/{provider_id}", token, json=payload))
            if result.provider_id != provider_id:
                raise provider_error("OLX_RESPONSE", "Identyfikator ogłoszenia OLX nie pasuje do zlecenia.")
            return result
        self._api(
            "POST",
            f"/adverts/{provider_id}/commands",
            token,
            json={"command": "deactivate", "is_success": True},
        )
        return PublicationResult("ended", provider_id, "removed_by_user")

    def find_external(self, draft_id, token):
        data = self._api("GET", "/adverts", token, params={"external_id": draft_id, "limit": 2})
        if not isinstance(data, list) or any(
            not isinstance(item, dict) or str(item.get("external_id")) != draft_id for item in data
        ):
            raise provider_error("OLX_RESPONSE", "OLX nie potwierdził wyniku sprawdzenia identyfikatora.")
        return data

    def reconcile(self, attempt, token):
        provider_id = attempt["provider_id"]
        if provider_id:
            result = advert_result(self._api("GET", f"/adverts/{provider_id}", token))
            if result.provider_id != provider_id:
                raise provider_error("OLX_RESPONSE", "OLX zwrócił ogłoszenie z innym identyfikatorem.")
            if attempt["operation"] == "update" and attempt["status"] == "verification_required":
                # An advert ID read-back cannot establish that all changed fields took effect.
                return None
            if attempt["operation"] == "end":
                return (
                    PublicationResult("ended", provider_id, result.provider_status)
                    if result.status == "inactive"
                    else None
                )
            return result
        data = self.find_external(attempt["draft_id"], token)
        return advert_result(data[0]) if len(data) == 1 else None


def public_description(fields):
    from models import FIELDS

    attrs = FIELDS[fields["category"]]["attributes"]
    details = ["Stan: " + fields["condition"]]
    details.extend(
        f"{attrs.get(code, code)}: {value}" for code, value in sorted(fields["attributes"].items()) if value
    )
    return fields["description"] + "\n\n" + "\n".join(details)
