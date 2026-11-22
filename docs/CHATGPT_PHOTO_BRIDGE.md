# ChatGPT photo bridge and listing workflow

This feature adds photo uploads to the same Home Assistant MCP connection that manages ULM.
It uses the Hub's existing HA credentials and the ULM integration token. The app listens on its
existing internal port. There is no public upload URL, URL fetcher or extra ChatGPT connection.

## ChatGPT native photo attachments (ULM 1.4.0)

The official HA MCP server does not propagate OpenAI's file metadata for custom LLM
tools. The companion HA integration now adds a narrowly scoped and reversible
compatibility adapter that adds `_meta["openai/fileParams"] = ["files"]` only to
`ulm_attach_chat_photos` on the existing `/api/mcp` URL.
The tool accepts a top-level `files` array; each object includes `download_url`,
`file_id`, and optionally `mime_type` and `file_name`.
ChatGPT injects those objects from chat attachments. File identifiers are opaque (no required `file_` prefix). Bare paths and file IDs without the ChatGPT-provided `download_url` cannot be uploaded. No separate plugin, ingress,
public upload endpoint or paid API key is needed.

The integration downloads the exact original over verified HTTPS with no redirects and
a DNS resolver that refuses non-public destinations, then sends encrypted chunks
through ULM's existing four upload tools. It does not log signed URLs or file
contents. The resulting draft stays unpublished.

This scoped formatter adaptation depends on private HA Core 2026.11 APIs; verify
it against the official Core build whenever HA Core is updated. The CI workflow
verifies the emitted tool metadata on a real Home Assistant Core instance.
Real ChatGPT-to-HAOS end-to-end transfer must still be verified after upgrading
both app and integration and refreshing SHWM tools.

## Which ChatGPT clients can send a photo?

The official [OpenAI plugin reference](https://developers.openai.com/plugins/reference#file-apis)
uses `_meta["openai/fileParams"]` to bind an uploaded file to a named tool argument. The
[HA Core 2026.11 MCP formatter](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/mcp_server/server.py)
does not emit this metadata for custom LLM tools. Passing a visible image to ChatGPT in a
regular conversation therefore does not, on its own, make the original available to ULM.

A client with actual original bytes can use the four upload tools on the same MCP endpoint.
In ChatGPT Work Mode, the assistant can use an authoritatively resolved Library attachment,
materialize it into a private workspace and encrypt and send its bytes programmatically.
File references, thumbnails and descriptions alone are insufficient. The availability of
Library files in Work Mode depends on the conversation and its access to the attachment.
The ordinary ChatGPT conversation flow requires a supported file capability that supplies
the original bytes. Until it is available, add the photos in the private ULM panel and use
ChatGPT for the draft. That is a manual import step.

## Workflow sprzedaży po polsku

1. Prześlij zdjęcia jednego przedmiotu. Jeśli rozpoznasz kilka różnych przedmiotów, przypisz
   zdjęcia osobno do każdego przedmiotu. Przy niejasnym przypisaniu poczekaj na właściciela.
2. Spisz to, co widać, w tym oznaczenia, ślady zużycia i wyposażenie. Markę, model i OEM wpisz
   jako potwierdzone tylko wtedy, gdy można je odczytać lub zweryfikować. Nie wyciągaj
   wniosków o sprawności ani o kompatybilności z konkretnym autem na podstawie podobieństwa.
   Hipotezy oraz pola do sprawdzenia zapisz w prywatnych `fields.notes`.
3. Przygotuj tytuł, opis i kategorię. Porównaj rzeczywiste oferty o porównywalnym stanie,
   jeśli źródła są dostępne i pozwalają odczytać dane. W `suggestion.sources` zapisz linki, a
   w `suggestion.notes` datę sprawdzenia, walutę i podstawę porównania. Zaznacz, czy to ceny
   ofertowe, czy sprzedaży. Bez źródeł ustaw `confidence=low` i nazwij wynik „szacunkiem”.
   Pozostaw cenę pustą, jeśli brakuje podstaw do oszacowania. `price_minor` jest w groszach.
4. Wywołaj `ulm_create_draft` ze stabilnym `request_id` albo pobierz najświeższą rewizję
   wskazanego szkicu przed jego edycją. Dołącz wyłącznie zatwierdzony przez użytkownika zestaw
   źródłowych plików. Wyślij je po kolei, korzystając z nowej rewizji po każdym zdjęciu.
   Ustaw `primary=true` tylko dla najlepszego zdjęcia głównego.
5. Sprawdź `ulm_list_media` i `ulm_get_review_preview`. Pokaż tytuł, cenę z podstawą,
   liczbę zdjęć, zdjęcie główne i pola do potwierdzenia. Szkic pozostaje do decyzji
   właściciela. Ten workflow kończy się na podglądzie. Zgoda i publikacja są poza nim.

## MCP and the private backend

| Tool | Arguments and result |
| --- | --- |
| `ulm_prepare_photo_upload` | `draft_id`, `expected_revision`, stable `request_id`, `filename`, `mime_type`, `size_bytes`, lowercase `sha256`, optional `primary`. Returns a session ID, X25519 public key, next byte offset, chunk size and expiry, or the original receipt for a completed request. |
| `ulm_upload_photo_chunk` | `session_id`, `offset`, `encrypted_chunk`. Returns the next byte offset. Repeating an already acknowledged chunk compares its decoded SHA-256 and leaves the cursor untouched. |
| `ulm_finish_photo_upload` | `session_id`. Returns the original's SHA-256 as `photo_id`, updated revision, image count, MIME, size, and cover ID. Assembly, byte verification, album update, approval revocation and receipt persist atomically with the album transaction. |
| `ulm_cancel_photo_upload` | `session_id`. Discards staged data for a pending transfer. A finished transfer returns its receipt. |

All four tools are mutations and disappear in HA MCP Read-only mode. The selected HA admin
is checked by the integration. The app validates the token and private network at
`POST /bridge/v1/tools/{name}`, as for the other HA tools. Read-only mode keeps the eight
existing read tools. The direct ULM MCP endpoint is unchanged.

Each upload is for one photo. Ordered chunks contain up to 48 KiB of source bytes. The last
chunk is exactly the remaining length. [The byte-client helper](../scripts/photo_bridge_client.py)
prepares requests and creates the packets. A packet is standard Base64 of the sender's
32-byte X25519 public key, 12-byte nonce, ChaCha20-Poly1305 ciphertext and 16-byte tag.
Derive the key with HKDF-SHA256 of the ECDH secret, the session UUID's 16 bytes as salt and
the UTF-8 bytes `ULM photo upload v1` as info. Bind the unsigned 4-byte big-endian chunk offset
as authenticated data. MCP and HA debug logs see ciphertext rather than the photo. Backend
logs omit file contents and bearer tokens. Keep encrypted chunks out of client logs as well.

ULM validates full size, SHA-256, image contents and matching MIME at the end. It retains exact
JPG, PNG and WebP originals, makes metadata-free thumbnails and rejects duplicates. Limits
are 12 MiB per photo and 20 photos per draft. Pending transfers survive a restart and expire
30 minutes after preparation. The app removes staged chunks and keys on cancellation,
completion or expiry and retains receipts for 24 hours. A stale listing revision fails
at finalization. Reread the draft, cancel the pending session and start a new request if
the owner still wants the upload. An interrupted connection can reuse the same request ID
and continue from the acknowledged offset. The original tool arguments must match on retry.

## Test and deployment

Update the HAOS app and HACS integration together. The app migrates a 1.2 database in place.
Back up the app and data before updating. Restore a pre-update backup for a rollback to 1.2.x.
The test workflow uses a real HA Core container and verifies exact original bytes and thumbnails,
cover order, restart during upload, draft preservation and Read-only behavior. That isolated
test is independent of the ChatGPT connector.

For live acceptance, read the agreed test draft and its publication status first. Use two
files specifically authorized for that test and compare the local SHA-256 values with the
two MCP receipts and ordered album. Read the thumbnails and recheck the draft fields,
revision, album and OLX state after a controlled app restart. Report the ChatGPT connector
path separately from the native Core/container path. Keep all test photographs, names
and hashes out of the repository and workflow logs.
