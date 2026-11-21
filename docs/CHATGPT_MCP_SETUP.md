# ChatGPT i prywatny MCP

Serwer działa lokalnie bez klucza OpenAI. Zweryfikowane zostały inicjalizacja i narzędzia Streamable HTTP przez klienta Python SDK. Połączenie z tym konkretnym serwerem na Twoim koncie ChatGPT jest osobnym etapem. Udokumentowana pełna obsługa własnych MCP w ChatGPT dotyczy Business i Enterprise/Edu. Dostęp do innych pluginów nie potwierdza uprawnień zapisu dla nowego połączenia. Zmiana planu nie jest wymagana do obsługi panelu ani alternatywy JSON. [Aktualna dokumentacja ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## Test serwera w prywatnej sieci

1. W opcji `mcp_bearer_token` ustaw losowy ciąg co najmniej 32 znaków w formacie base64url, np. wynik `python -c "import secrets; print(secrets.token_urlsafe(36))"`. Nie wklejaj go do rozmowy. Ustaw `mcp_enabled=true`, zapisz, zrestartuj.
2. Klient w tej samej sieci aplikacji HA łączy się z `http://NAZWA-APLIKACJI:8100/mcp`. Na komputerze można uruchomić wersję deweloperską opisaną poniżej i użyć `http://127.0.0.1:8100/mcp`. W Inspectorze wybierz Streamable HTTP i ustaw nagłówek `Authorization: Bearer <token>`.
3. Wywołaj `health`, `list_drafts`, `create_draft` z `fields: {"title":"Test prywatny"}` i nowym `request_id`, a potem `update_draft` z oddanym `expected_revision` i `changes: {"price_minor":15000}`. Sprawdź utrzymanie identyfikatora po powtórzeniu utworzenia i błąd przy nieaktualnej rewizji. Bez tokenu serwer musi zwrócić 401.
4. Przejdź do panelu HA i odszukaj szkic. Narzędzie `listings_approve` daje instrukcję do podglądu. Nie udziela zgody.

### Wersja deweloperska

W katalogu źródeł, na Linux/macOS:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install --only-binary=:all: -r olx_manager/requirements.txt
export DEV_PANEL=true DATA_DIR="$PWD/dev-data" MCP_ENABLED=true
export MCP_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(36))')"
python olx_manager/app/server.py
```

Panel jest dostępny pod `http://127.0.0.1:8099/`. Możesz użyć [MCP Inspector](https://github.com/modelcontextprotocol/inspector) albo testu klienta `python -m unittest discover -s tests -p test_mcp.py -v`. Testy wymagają wolnych portów 8099 i 8100.

## Połączenie bezpośrednie z ChatGPT przez HTTPS i OAuth

Warunek to uprawnienie do prywatnego własnego MCP na koncie oraz osiągalny reverse proxy i własny dostawca OAuth. Możesz użyć istniejącego dostawcy, który wystawia JWT z audiencją zasobu oraz obsługuje discovery, Authorization Code z PKCE i rejestrację klienta metodą dostępną w konfiguratorze ChatGPT. W tym wariancie aplikacja jest serwerem zasobu OAuth, a logowanie i odświeżanie sesji zapewnia wystawca.

1. Na [stronie Plugins w ChatGPT](https://chatgpt.com/plugins) sprawdź, czy po kliknięciu **+** widzisz **Add custom MCP server**. Jeżeli funkcja jest dostępna, sprawdź też, czy uprawnienia opcjonalnych narzędzi obejmują zapis. Ustawienia starszego interfejsu mogą być w **Settings → Apps → Create**.
2. U dostawcy OAuth zarejestruj pełny zasób `https://<domena-mcp>/mcp`, zakres `listings:manage` i tylko konto właściciela. Zezwól na PKCE S256. Wprowadź dokładny callback klienta OpenAI, który pokazuje kreator lub aktualna dokumentacja. Dostawca musi nadawać podpisany JWT RS256 lub ES256 z `aud` pełnego zasobu, `sub` właściciela i tekstowym `scope`. Włącz refresh i `offline_access`, jeżeli używa go dostawca.
3. W opcjach aplikacji włącz MCP. Wypełnij `mcp_resource_url`, `mcp_issuer`, `mcp_jwks_url` i `mcp_subject`. Pozostaw `mcp_bearer_token` pusty w tym wariancie i uruchom ponownie. Proxy ma przepuszczać tylko `/mcp` i `/.well-known/oauth-protected-resource` oraz `/.well-known/oauth-protected-resource/mcp` na port 8100. Przekazuj nagłówek Authorization. Wymagaj TLS na publicznym adresie i wyłącz logowanie nagłówków uwierzytelnienia. Użyj nazwy hosta aplikacji pokazywanej w karcie Informacje aplikacji. Ma ona postać prefiksu repozytorium oraz sluga z myślnikami.
4. Sprawdź z zewnątrz, że `GET /.well-known/oauth-protected-resource/mcp` zwraca poprawny zasób i issuer, a `POST /mcp` bez tokenu zwraca 401 i `WWW-Authenticate`. Wykonaj te same próby z tokenem innego użytkownika albo audiencji, oczekując 401.
5. W ChatGPT wybierz **Plugins → + → Add custom MCP server**, podaj pełne HTTPS `/mcp`, wybierz OAuth i zaloguj się u wystawcy. Przeskanuj narzędzia, dodaj prywatnie i wybierz w nowej rozmowie. Wywołaj `list_drafts`, utwórz szkic testowy, zmień jego cenę i sprawdź dane w HA. Po zatwierdzeniu w panelu testuj tylko `publish_approved(mode="mock")`. Sprawdź odnowienie sesji, cofnięcie zgody i brak dostępu po zmianie uprawnień.

OpenAI opisuje również opcjonalny [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). Wymaga uprawnień do tunelu i klienta uruchomionego w prywatnej sieci. Wydanie nie zawiera ani nie wdraża klienta tunelu. Gotowa ścieżka konfiguracji tego wydania to HTTPS z OAuth. Możliwość podłączenia tunelu i dopuszczalną metodę auth należy potwierdzić w docelowym koncie.

## Zdjęcia i alternatywa dla Plus

ChatGPT obsługuje udokumentowane parametry plikowe pluginu oznaczone `openai/fileParams`, które mogą przekazać `file_id` i czasowy `download_url`. Ta dostępność zależy od typu połączenia i dostępnych funkcji konta. Transfer obrazu z tej rozmowy do tego prywatnego serwera nie został uruchomiony end-to-end. Wydanie celowo nie wystawia narzędzia pobierającego URL załącznika.

1. Wyślij fotografie w ChatGPT do analizy. Zweryfikuj odczytane numery i stan. Poproś o źródła ceny, a gdy ich brakuje, o jawne oznaczenie szacunku.
2. Jeśli prywatne MCP działa w ChatGPT, poproś o zapis szkicu. Jeśli zapis jest niedostępny, otwórz **Import z ChatGPT** na pulpicie i wklej przygotowany JSON. Oryginalne zdjęcia wybierz w panelu HA. Alternatywnie skopiuj je z Nextcloud lokalnie i dodaj przez panel lub udział `/share/olx_manager/inbox`.
3. Wskaż zdjęcie główne, przejdź do podglądu i zatwierdź. Jeśli MCP jest połączone, wróć do rozmowy i wydaj osobne polecenie publikacji. W alternatywie użyj przycisku z panelu. OLX live wymaga osobnej konfiguracji niezależnie od planu ChatGPT.
4. Gdy album jest już na HA, działający klient MCP odczyta metadane `list_media` i pojedynczą miniaturę JPEG przez `get_image`. Klient ChatGPT musi także obsłużyć obraz zwracany przez narzędzie. Oryginały zostają prywatne.

Dokumentacja. [OAuth pluginu](https://developers.openai.com/plugins/build/auth) · [Testowanie połączenia](https://developers.openai.com/plugins/deploy/connect-chatgpt) · [Parametry plikowe](https://developers.openai.com/plugins/reference).
