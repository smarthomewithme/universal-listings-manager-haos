import logging
import sqlite3
from contextlib import nullcontext

from marketplace import MockMarketplaceAdapter, PublicationResult, provider_error
from media import DomainError, sha256
from store import EDITABLE, ident, now

LOG = logging.getLogger(__name__)
UNCERTAIN = {"OLX_NETWORK", "OLX_RESPONSE"}
REAL_PENDING = {"publishing", "verification_required", "submitted", "payment_required"}
PUBLIC = {"published", "submitted", "payment_required", "inactive", "ended"}


class PublicationService:
    def __init__(self, store, olx, settings):
        self.store, self.olx, self.settings = store, olx, settings
        self.mock = MockMarketplaceAdapter()

    def _last(self, db, draft_id, mode, statuses=None):
        rows = db.execute(
            "SELECT * FROM marketplace_listings WHERE draft_id=? AND mode=? ORDER BY rowid DESC",
            (draft_id, mode),
        )
        return next((dict(r) for r in rows if statuses is None or r["status"] in statuses), None)

    def status(self, draft_id):
        with self.store.connect() as db:
            draft = self.store._get(db, draft_id)
            real = self._last(db, draft_id, "olx")
            simulated = self._last(db, draft_id, "mock")
        account = self.olx.status()
        awaiting = (
            account["status"] == "OLX_NOT_CONFIGURED"
            or not account["write_enabled"]
            or not account["no_charge_confirmed"]
            or not self.settings.olx_public_base
            or draft["fields"]["olx_category_id"] not in self.settings.allowed_categories
        )
        if draft["status"] == "approved":
            if draft["approval"]["expired"]:
                message = "Zatwierdzenie wygasło. Potrzebna jest ponowna zgoda."
            elif account["status"] == "OLX_NOT_CONFIGURED":
                message = "Zatwierdzone, oczekuje na połączenie OLX"
            elif awaiting:
                message = "Zatwierdzone, uzupełnij ustawienia publikacji OLX"
            else:
                message = "Zatwierdzone, gotowe do weryfikacji i publikacji"
        else:
            message = ""
        public_keys = (
            "id",
            "revision",
            "mode",
            "operation",
            "status",
            "provider_id",
            "provider_status",
            "provider_url",
            "error",
            "created_at",
        )
        return {
            "draft_id": draft_id,
            "status": draft["status"],
            "message": message,
            "olx": {k: real[k] for k in public_keys} if real else None,
            "demo": {k: simulated[k] for k in public_keys} if simulated else None,
        }

    def _approved(self, db, draft_id, revision):
        draft = self.store._check(db, draft_id, revision, {"approved"})
        approval = draft["approval"]
        if not approval or approval["expired"] or approval["digest"] != self.store.review_digest(draft):
            raise provider_error(
                "APPROVAL_INVALID", "Zatwierdzenie wygasło lub treść ogłoszenia uległa zmianie."
            )
        if self.store.missing(draft):
            raise provider_error("REVIEW_INCOMPLETE", "Brakuje wymaganych danych szkicu.")
        return draft

    def _check_all_photos(self, db, draft):
        for photo in draft["media"]:
            row = db.execute("SELECT original FROM media_assets WHERE id=?", (photo["id"],)).fetchone()
            if not row or sha256(row["original"]) != photo["id"]:
                raise provider_error("IMAGE_INTEGRITY", "Zdjęcie różni się od zatwierdzonego oryginału.")

    def _reserve(self, db, draft, mode, operation, account_id, provider_id, actor):
        if mode == "olx":
            count = db.execute(
                "SELECT count(*) FROM marketplace_listings WHERE mode='olx' AND created_at>?",
                (now() - 86400,),
            ).fetchone()[0]
            if count >= self.settings.olx_daily_limit:
                raise provider_error("PUBLICATION_LIMIT", "Osiągnięto dobowy limit zapisów OLX.", 429)
        attempt = {
            "id": ident(),
            "draft_id": draft["id"],
            "revision": draft["revision"],
            "digest": self.store.review_digest(draft),
            "mode": mode,
            "operation": operation,
            "account_id": account_id,
            "provider_id": provider_id,
            "created_at": now(),
        }
        try:
            db.execute(
                """
                INSERT INTO marketplace_listings(id,draft_id,revision,digest,mode,operation,account_id,
                    provider_id,created_at,status) VALUES (:id,:draft_id,:revision,:digest,:mode,:operation,
                    :account_id,:provider_id,:created_at,'publishing')
            """,
                attempt,
            )
        except sqlite3.IntegrityError as err:
            raise provider_error(
                "DUPLICATE_SUBMISSION", "Ta wersja lub identyczna oferta została już wysłana."
            ) from err
        if mode == "olx":
            db.execute("UPDATE drafts SET status='publishing', updated_at=? WHERE id=?", (now(), draft["id"]))
            if operation != "end":
                db.executemany(
                    "INSERT INTO publication_media VALUES (?,?,?)",
                    [(attempt["id"], p["id"], p["rotation"]) for p in draft["media"]],
                )
        self.store.audit(db, draft["id"], actor, f"{mode}_{operation}_reserved", draft["revision"])
        return attempt

    def _record(self, attempt, result, actor, error=None):
        mode, draft_id = attempt["mode"], attempt["draft_id"]
        with self.store.connect(write=True) as db:
            stored = db.execute("SELECT * FROM marketplace_listings WHERE id=?", (attempt["id"],)).fetchone()
            provider_id = result.provider_id or stored["provider_id"]
            db.execute(
                """
                UPDATE marketplace_listings SET status=?, provider_id=?, provider_status=?, provider_url=?, error=?
                WHERE id=?
            """,
                (result.status, provider_id, result.provider_status, result.url, error, attempt["id"]),
            )
            if mode == "olx":
                states = {
                    "verification_required": "verification_required",
                    "submitted": "submitted",
                    "payment_required": "submitted",
                    "published": "published",
                    "ended": "sold",
                    "inactive": "archived",
                    "failed": "failed",
                }
                db.execute(
                    "UPDATE drafts SET status=?, updated_at=? WHERE id=? AND revision=?",
                    (states[result.status], now(), draft_id, attempt["revision"]),
                )
            self.store.audit(db, draft_id, actor, f"{mode}_{result.status}", attempt["revision"])

    def recover_interrupted(self):
        with self.store.connect(write=True) as db:
            db.execute("""
                UPDATE drafts SET status='verification_required' WHERE id IN
                (SELECT draft_id FROM marketplace_listings WHERE status='publishing' AND mode='olx')
            """)
            db.execute("""
                UPDATE marketplace_listings SET status=CASE WHEN mode='olx' THEN 'verification_required' ELSE 'failed' END,
                    error='SERVICE_RESTARTED' WHERE status='publishing'
            """)

    def publish(self, draft_id, revision, mode, actor):
        if mode == "mock" and not self.settings.mock_enabled:
            raise provider_error("MOCK_DISABLED", "Tryb demonstracyjny jest wyłączony.")
        lock = self.olx.lock if mode == "olx" else nullcontext()
        with lock:
            return self._publish(draft_id, revision, mode, actor)

    def _publish(self, draft_id, revision, mode, actor):
        with self.store.connect() as db:
            last = self._last(db, draft_id, mode)
            if not last or last["revision"] != revision or last["operation"] == "end":
                draft = self._approved(db, draft_id, revision)
                last = None
        if last:
            if mode == "olx" and last["status"] in {"verification_required", "submitted"}:
                return self.refresh(draft_id, actor)
            return self.status(draft_id)
        if mode == "mock":
            with self.store.connect(write=True) as db:
                last = self._last(db, draft_id, mode)
                if not last or last["revision"] != revision:
                    draft = self._approved(db, draft_id, revision)
                    self._check_all_photos(db, draft)
                    attempt = self._reserve(db, draft, "mock", "create", "local", None, actor)
                    result = self.mock.submit(attempt, draft)
                    db.execute(
                        "UPDATE marketplace_listings SET status=?,provider_id=?,provider_status=?,provider_url=? WHERE id=?",
                        (
                            result.status,
                            result.provider_id,
                            result.provider_status,
                            result.url,
                            attempt["id"],
                        ),
                    )
                    self.store.audit(db, draft_id, actor, "mock_published", revision)
            return self.status(draft_id)
        if not self.olx.status()["write_enabled"] or self.olx.status()["status"] == "OLX_NOT_CONFIGURED":
            self.olx.write_account()  # Raises the precise gate error without a provider request.
        if draft["fields"]["olx_category_id"] not in self.settings.allowed_categories:
            raise provider_error("OLX_CATEGORY_CLOSED", "Kategoria OLX nie jest na zaakceptowanej liście.")
        account = self.olx.write_account()
        payload = self.olx.prepare(draft, account)
        with self.store.connect() as db:
            prior = self._last(db, draft_id, "olx", PUBLIC | REAL_PENDING)
        operation = "create"
        provider_id = None
        if prior:
            if prior["status"] != "published" or prior["account_id"] != account["user_id"]:
                raise provider_error(
                    "OLX_ALREADY_SUBMITTED", "Sprawdź poprzednią próbę publikacji i użyte konto."
                )
            operation = "update"
            provider_id = prior["provider_id"]
        elif self.olx.find_external(draft_id, account["access_token"]):
            raise provider_error(
                "OLX_EXTERNAL_ALREADY_EXISTS",
                "OLX już ma ofertę o tym identyfikatorze. Sprawdź ją przed zapisem.",
            )
        with self.store.connect(write=True) as db:
            draft = self._approved(db, draft_id, revision)
            self._check_all_photos(db, draft)
            attempt = self._reserve(db, draft, "olx", operation, account["user_id"], provider_id, actor)
        return self._send(attempt, draft, account["access_token"], payload, actor)

    def _send(self, attempt, draft, token, payload, actor):
        try:
            result = self.olx.submit(attempt, draft, token, payload)
            self._record(attempt, result, actor)
        except DomainError as err:
            status = "verification_required" if err.code in UNCERTAIN else "failed"
            self._record(attempt, PublicationResult(status), actor, err.code)
        except Exception:  # noqa: BLE001 - Fail closed after a potentially completed provider write.
            # Never log a provider payload, token, URL or exception message.
            LOG.error("Niejednoznaczny wynik zapisu OLX. Potrzebna weryfikacja.")
            self._record(attempt, PublicationResult("verification_required"), actor, "OLX_UNCERTAIN")
        return self.status(draft["id"])

    def refresh(self, draft_id, actor):
        with self.olx.lock:
            with self.store.connect() as db:
                attempt = self._last(db, draft_id, "olx")
            if not attempt or attempt["status"] not in PUBLIC | {"verification_required"}:
                return self.status(draft_id)
            account = self.olx.account()
            if account["user_id"] != attempt["account_id"]:
                raise provider_error("OLX_ACCOUNT_MISMATCH", "Połącz konto, które wysłało ofertę.")
            result = self.olx.reconcile(attempt, account["access_token"])
            current = (
                attempt["status"],
                attempt["provider_id"],
                attempt["provider_status"],
                attempt["provider_url"],
            )
            if result and (result.status, result.provider_id, result.provider_status, result.url) != current:
                self._record(attempt, result, actor)
            return self.status(draft_id)

    def end(self, draft_id, revision, actor):
        with self.olx.lock:
            with self.store.connect() as db:
                last = self._last(db, draft_id, "olx")
                if not last or last["status"] != "published":
                    raise provider_error(
                        "OLX_NOT_ACTIVE", "Ogłoszenie nie jest potwierdzone jako aktywne na OLX."
                    )
                draft = self.store._check(db, draft_id, revision, EDITABLE | {"published"})
            account = self.olx.write_account()
            if account["user_id"] != last["account_id"]:
                raise provider_error("OLX_ACCOUNT_MISMATCH", "Zaloguj konto, które opublikowało tę ofertę.")
            with self.store.connect(write=True) as db:
                draft = self.store._check(db, draft_id, revision, EDITABLE | {"published"})
                attempt = self._reserve(
                    db, draft, "olx", "end", account["user_id"], last["provider_id"], actor
                )
            return self._send(attempt, draft, account["access_token"], {}, actor)

    def signed_photo(self, attempt_id, asset_id):
        with self.store.connect() as db:
            row = db.execute(
                """
                SELECT original AS data, publication_media.rotation FROM publication_media
                JOIN media_assets ON asset_id=media_assets.id
                JOIN marketplace_listings ON attempt_id=marketplace_listings.id
                WHERE attempt_id=? AND asset_id=?
                AND marketplace_listings.status IN ('publishing','submitted','published','verification_required')
            """,
                (attempt_id, asset_id),
            ).fetchone()
            if row and sha256(row["data"]) == asset_id:
                return dict(row)
        raise provider_error("IMAGE_NOT_FOUND", "Zdjęcie niedostępne.", 404)
