# Pierwsze uruchomienie

1. W Home Assistant otwórz **Ustawienia → Aplikacje → Zainstaluj aplikację → ⋮ → Repozytoria**. Wklej adres publicznego repozytorium Universal Listings Manager i kliknij **Dodaj**. Repozytorium GitHub musi mieć już gotowe wydanie na gałęzi `main`.
2. Otwórz **Universal Listings Manager → Zainstaluj → Uruchom**. Na mini PC x86-64 HA pobierze gotowy obraz. Włącz **Pokaż na pasku bocznym**, aby mieć pozycję **Ogłoszenia**.
3. Kliknij **Otwórz interfejs WWW**. Skopiuj wyświetlony identyfikator swojego konta HA, wpisz go na karcie **Konfiguracja** w polu `owner_user_id`, zapisz i zrestartuj aplikację.
4. Utwórz przykładowy szkic i wpisz tytuł, opis, stan, miejscowość i cenę. Dodaj zdjęcia JPEG, PNG lub WebP w albumie. Wybierz **Podgląd → Przekaż do zatwierdzenia → Zatwierdź ogłoszenie → Symuluj publikację**.

## Aktualizacje

W **Aplikacje → Zainstaluj aplikację → ⋮ → Sprawdź aktualizacje**, odśwież listę. Gdy pojawi się nowsza wersja, otwórz aplikację i wybierz **Aktualizuj**. Instalacja zachowuje identyfikator wynikający z adresu repozytorium i sluga aplikacji. Zostaw aktualizacje ręczne na pierwszy odbiór. Możesz później włączyć **Automatyczne aktualizacje** na karcie aplikacji.

Przed aktualizacją zrób kopię zapasową aplikacji w **Ustawienia → System → Kopie zapasowe**. Backup aplikacji zawiera jej opcje oraz prywatne `/data` z bazą, obrazami i kluczem szyfrującym tokeny. Odzyskuj dane i wersję razem z tej samej kopii w HA. Po przywróceniu starszego backupu pozostaw zapis do OLX wyłączony do porównania zewnętrznych ogłoszeń z lokalnym stanem.

### Wcześniejsza instalacja lokalna

Aplikacja z katalogu lokalnego i aplikacja z repozytorium GitHub to różne instalacje Supervisora, nawet przy tym samym slugu. Jeżeli v1.0 była już używana, zachowaj jej aplikację i kopię. Przeniesienie do innego ID wymaga kontrolowanej migracji danych. Jeśli aplikacji z ZIP-a nigdy nie zainstalowano, wykonaj świeżą instalację z GitHuba.

## Istniejące połączenie MCP Home Assistant

Wersja 1.2.0 pozwala dodać narzędzia ULM do istniejącego głównego MCP HA. Wymaga komponentu
**Universal Listings Manager** instalowanego z tego samego repozytorium przez HACS oraz
osobnego tokenu `ha_llm_token`. [Instrukcja integracji centralnej](https://github.com/smarthomewithme/universal-listings-manager-haos/blob/main/docs/HA_MCP_SETUP.md).

## ChatGPT i OLX

- W scenariuszu podstawowym przygotuj opis w ChatGPT i użyj **Import z ChatGPT**. Oryginały zdjęć dodaj w panelu. Bezpośredni zapis wymaga skonfigurowanego, uwierzytelnionego MCP i uprawnień na koncie ChatGPT. Domyślnie MCP jest wyłączony.
- Tryb demonstracyjny działa bez konta OLX. Prawdziwy zapis OLX wymaga zatwierdzonej aplikacji Partner API, OAuth, dozwolonej kategorii, potwierdzonych warunków cenowych i świadomego włączenia opcji. Zatwierdzenie oferty potwierdza właściciel w panelu, a publikacja jest osobną operacją.
- Źródła instalatora opisują konfigurację MCP, OLX, kopie i ograniczenia w sekcji Dokumentacja repozytorium.
