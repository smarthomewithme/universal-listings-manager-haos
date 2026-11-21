# Universal Listings Manager 1.0.0

## Zweryfikowane lokalnie

- Panel po polsku z formularzami dla części samochodowych, elektroniki, narzędzi, mebli, AGD i innych przedmiotów.
- Album z wielokrotnym wyborem JPEG, PNG i WebP. Zachowanie oryginału, miniatura, wybór okładki, kolejność, obrót i usuwanie.
- Podgląd, zgoda właściciela, cofnięcie zgody po edycji, rewizje, historia, duplikowanie i archiwizacja.
- Trwałe dane i kopia SQLite. Migracja szkiców z v0.1.
- Prywatny MCP Streamable HTTP z 27 narzędziami, test klienta SDK oraz sprawdzanie uwierzytelnienia.
- Symulacja OLX z linkiem do wyniku w panelu. Import danych oferty z ChatGPT w formacie JSON.

## Konfiguracja właściciela

- Przypisz własny identyfikator użytkownika HA w opcji `owner_user_id`.
- Dla MCP włącz nasłuch i ustaw uwierzytelnienie. Zdalny wariant HTTPS wymaga wystawcy OAuth i proxy ograniczonego do ścieżek MCP.
- Publikacja jest osobnym poleceniem po panelowym zatwierdzeniu. Każda nowa wersja aktywnej oferty przechodzi tę samą procedurę.

## Wymaga dostępu OLX

Prawdziwy zapis wymaga aplikacji aktywowanej w OLX Developer, OAuth konta prywatnego, potwierdzonej kategorii i kosztów, HTTPS dla callbacku i podpisanych zdjęć oraz świadomego włączenia `olx_write_enabled`. Wersja obsługuje zapis z odbiorem osobistym. Wysyłkę można zapisać w szkicu i skorzystać z niego jako materiału do ręcznego wystawienia. Zakup pakietów i promowań pozostaje poza zakresem.

## Do odbioru w środowisku docelowym

Docker build, faktyczna instalacja przez Supervisora HAOS, aplikacja mobilna HA i połączenie nowego prywatnego MCP z Twoim ChatGPT Plus wymagają oddzielnego testu. Bezpośredni transfer załącznika z ChatGPT nie jest częścią wdrożonego przepływu. Udostępniono działający import danych i upload zdjęć z telefonu. Treść przygotowuje ChatGPT bez dodatkowego płatnego modelu na serwerze.
