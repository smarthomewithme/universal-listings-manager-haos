# Raport odbioru Universal Listings Manager 1.0.0

Weryfikacja 21 listopada 2026 r. Środowisko testowe Linux x86-64, Python 3.12, MCP SDK 2.3.0, Playwright 1.62.0 i Chrome Headless Shell 151.

**Wynik: 30 testów PASS.** Testy objęły aplikację HTTP, mobilny widok Chromium, prawdziwy transport MCP oraz kontrakt OLX na atrapach HTTP. Żadna operacja testowa nie została wysłana na prawdziwe konto OLX.

Commit implementacji w załączonym repozytorium Git: `dc64a7a8ec8d72ce7431f29cf61112b0160643e8`. Pakiet zawiera kompletny kod, historię Git w pliku bundle, pliki do instalacji HAOS i workflow CI. Nie było wdrożenia ani zmian na HA właściciela.

## Kryteria

| # | Scenariusz | Wynik i dowód |
|---:|---|---|
| 1 | Regresje prototypu, jednostkowe, lint, format | **PASS**. Zapis i edycja, konflikt, brak zdjęcia, bezpieczny import, trwałość. Poprawne pliki zastąpiły w testach samą sygnaturę PNG. Ruff, format, Node syntax, compileall i pip check przeszły |
| 2 | amd64, build Docker, health, transport | **PASS** dla procesu na x86-64, `GET /health`, inicjalizacji MCP, 27 narzędzi, odczytu/zapisu i obrazu JPEG z klienta SDK. **NOT TESTED** dla builda i uruchomienia obrazu Docker. Weryfikacja przypiętego manifestu amd64 przeszła. Daemon Docker nie wystartował, ponieważ środowisko odrzuciło gniazdo Unix |
| 3 | Mobilny przebieg i zdjęcia | **PASS** w rzeczywistym Chromium z emulowaną ścieżką Ingress. Dwie kategorie, trzy dekodowalne pliki testowe JPEG/PNG/WebP, okładka, kolejność, obrót, usunięcie i podgląd bez poziomego przewijania przy 390 i 320 px |
| 4 | Edycja i wygasanie zgody | **PASS**. Cena, dodanie, usunięcie, obrót i zmiana kolejności zdjęć cofają zgodę. Test wygasłej zgody i konfliktu rewizji |
| 5 | Restart, kopia i migracja | **PASS**. Restart procesu HTTP zachował szkic i identyczne bajty oryginału. Test migracji v0.1 zachował ID, treść, cenę i poprawny obraz. Test kopii SQLite z odtworzeniem |
| 6 | OLX OAuth, symulacja, brak live bez dostępu | **PASS** na atrapach HTTP. OAuth state jednorazowe, token szyfrowany i odświeżany, brak prawdziwego zapisu bez OAuth lub włączenia. Tryb demo ma własny lokalny link i status |
| 7 | Ochrona panelu, MCP i zdjęć | **PASS**. Drugi użytkownik HA, nieprawidłowy hop, CSRF, brak tokenu MCP, błędne konto, zakres, issuer, audiencja i termin JWT. Podpisane obrazy są pojedyncze, czasowe i bez EXIF. **NOT TESTED** dla publicznego proxy TLS i zewnętrznego dostawcy OAuth |
| 8 | Digest, opłaty, limity, duplikaty i awarie | **PASS** na atrapach. Test braku zgody, zmiany bajtów zdjęcia, nieautoryzowanej kategorii, braku atrybutu, opłat, konfliktu równoległego, duplikatu, utraty odpowiedzi i przerwania po rezerwacji. Ponowienie wykonuje tylko odczyt. Test aktualizacji i dezaktywacji przez właściwe ścieżki OLX |
| 9 | Prywatny HAOS, ChatGPT Plus i rzeczywiste OLX | **NOT TESTED**. Brak uruchomionego wdrożenia, dostępnego adresu nowej aplikacji, konfiguracji OAuth i poświadczeń OLX. |
| 10 | Załącznik z rozmowy ChatGPT do tego MCP | **NOT TESTED** i funkcja poza wydaniem. **PASS** dla alternatywy przez panel: import JSON i upload oryginałów. **PASS** dla odczytu miniatury z lokalnego MCP |
| 11 | Zależności | **PASS**. `pip-audit` na wszystkich 33 przypiętych pakietach nie znalazł znanych podatności w chwili sprawdzenia |

## Wykonane polecenia

Po instalacji zależności z `olx_manager/requirements.txt` oraz Ruff, pip-audit i Playwright:

```bash
ULM_BROWSER_TEST=1 ULM_BROWSER_EXECUTABLE=/sciezka/do/chrome-headless-shell python -m unittest discover -s tests -v
ruff check olx_manager/app tests scripts
ruff format --check olx_manager/app tests scripts
node --check olx_manager/app/static/app.js
python -m compileall -q olx_manager/app tests scripts
python -m pip check
pip-audit -r olx_manager/requirements.txt --no-deps --disable-pip
```

Sprawdzono YAML instalatora, wartości domyślne i plik repozytorium. Sprawdzono platformę `linux/amd64` w oficjalnym indeksie obrazu Python przypiętym w Dockerfile. CI w paczce buduje `docker build --platform=linux/amd64 -t ulm:smoke olx_manager` i odpala smoke-test SDK wewnątrz obrazu w środowisku z Dockerem. Workflow jest dostarczony, jego uruchomienie w GitHub Actions pozostaje do wykonania.

## Ograniczenia i świadome decyzje

- Jedno konto właściciela HA. Zdjęcia są przesyłane z panelu lub importowane z lokalnego inbox. Wydanie przyjmuje JPG, PNG i WebP. Do albumu załączniki z czatu należy dodać z telefonu albo z folderu.
- Zapis przez OLX dotyczy ofert z odbiorem osobistym i wymaga potwierdzenia braku opłat i zużycia pakietów. Wysyłka jest przechowywana w szkicu. Adapter nie wystawia ofert w wymagających nieobsługiwanych reguł kategoriach. Brak zakupów pakietów i automatycznych obniżek.
- Przy niejednoznacznej aktualizacji OLX sam odczyt identyfikatora nie potwierdza całej treści. Sprawdź ofertę bezpośrednio na OLX, przed jakąkolwiek kolejną ręczną operacją. Serwer nie ponawia takiej aktualizacji.
- ChatGPT prowadzi analizę i rozmowę. Zatwierdzenie zawsze następuje w HA. Publikacja jest osobnym poleceniem. Dostawca OAuth używany przez zdalne MCP jest niezależny od OAuth OLX.
- Migracja zachowuje oryginały v0.1. Niepoprawne pliki lub pliki przekraczające limity nie pojawiają się w aktywnym albumie. Stare akceptacje nie są ważne dla nowej wersji formularza.
- Koszty podstawowej obsługi to istniejąca infrastruktura i plan ChatGPT. Zewnętrzny OAuth, domena lub inne zależności łączności mogą podlegać cennikowi dostawcy. OLX ustala uprawnienia i koszty publikacji dla konkretnych kategorii.

## Odbiór właściciela

1. Skopiuj `olx_manager` do `local_apps`, zainstaluj w HA i przypisz `owner_user_id`.
2. Na telefonie dodaj zdjęcia, zapisz ofertę, wykonaj podgląd oraz symulację. Po restarcie otwórz to samo ogłoszenie.
3. Przygotuj chroniony MCP według przewodnika i przetestuj na tym samym koncie ChatGPT, czy widzisz odczyt i zapis szkicu. Gdy zapis nie jest dostępny, przejdź przez import JSON.
4. Utrzymaj `olx_write_enabled=false` do potwierdzenia warunków przez OLX. Podczas tego odbioru użyj wyłącznie symulatora.

Audyt źródła porównawczego znajduje się w `docs/AUDYT_UPSTREAM.md`.
