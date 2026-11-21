# Audyt olx-automation

Repozytorium [Waldisurfer/olx-automation](https://github.com/Waldisurfer/olx-automation) sklonowane do odczytu na commicie `67aac14411056c28a8e55427895d391d5fd985b0`. Licencja MIT w repo. Nie kopiowano plików ani kodu źródłowego. Zmiany i testy dotyczą osobnego Universal Listings Manager zbudowanego na prototypie dostarczonym przez właściciela.

| Obszar | Zaobserwowane w upstream | Rozwiązanie i test |
|---|---|---|
| Lokalizacja | `ListingPublisher.ts` używa sztywnej mapy i fallbacku Warszawa | ID oraz nazwa miasta i dzielnicy są weryfikowane z OLX. Błędna miejscowość blokuje zapis |
| Sprzedane | `ListingPublisher.ts` wywołuje aktywację w `markSold` | Uwierzytelniony właściciel używa komendy `deactivate` dopiero po potwierdzeniu. Atrapowy test sprawdza metodę i payload |
| Baza | README, implementacja i migracje w sprawdzonym commicie korzystają z PostgreSQL. Oczekiwanej mieszanki SQLite/PostgreSQL nie znaleziono | HAOS wykorzystuje wyłącznie SQLite z WAL, migracją prototypu i testem odtworzenia |
| Uwierzytelnianie | `AppConfig.ts` ma domyślny sekret JWT | Losowy token lokalny albo podpisywany JWT skonfigurowanego dostawcy. Brak domyślnego klucza. Testy braku autoryzacji i błędnych claims |
| Własność | Lista korzysta z właściciela, ale kilka tras według ID czyta i zmienia ogłoszenie bez filtra właściciela w tej samej ścieżce | Jeden jawnie przypisany właściciel, kontrola na wszystkich ścieżkach panelu i osobne uwierzytelnienie MCP. Test drugiego użytkownika i podrobionego nagłówka |
| OLX OAuth | Tokeny w pojedynczym rekordzie tekstowym. Publiczny callback wymienia kod bez sprawdzenia `state` | Zaszyfrowany rekord z lokalnym kluczem. Jednorazowe `state`, test złego i powtórzonego stanu oraz refresh |
| Zdjęcia | Express udostępnia `/uploads` przez statyczny katalog | Oryginał i miniatura wymagają właściciela. Do OLX tylko czasowa podpisana kopia. Test braku podpisu, wygaśnięcia i usunięcia metadanych |
| Ceny | `PriceReductionJob.ts` samoczynnie inicjuje obniżki | Brak harmonogramu obniżek. Nowa cena aktywnej oferty wymaga nowego zatwierdzenia i polecenia zapisu |
| Analiza i wyszukiwanie | Claude i scraping OLX są częścią przepływu aplikacji | ChatGPT prowadzi rozmowę i przygotowuje treść. Sugestie cen mają źródła i poziom pewności. Adapter używa Partner API wyłącznie zgodnie z przeznaczeniem |

Nie instalowano ani nie wykonywano upstream. Audyt jest przeglądem wskazanego snapshotu kodu i zgodności jego rozwiązań z zadaniem.
