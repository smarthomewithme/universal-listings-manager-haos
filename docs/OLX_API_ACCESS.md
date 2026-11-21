# OLX Partner API dla konta prywatnego

Start aplikacji i tryb testowy działają bez konta OLX. Domyślne opcje mają `olx_write_enabled: false`. Aplikacja nie wykonuje testowych publikacji na publicznym OLX.

## Przed konfiguracją

1. W [portalu deweloperskim OLX](https://developer.olx.pl/docs/getting-access-to-api) zaloguj się kontem OLX Polska. Portal wymaga zgłoszenia aplikacji i akceptacji. Formularz partnera pyta m.in. o nazwę firmy i NIP. Przy koncie prywatnym poproś OLX o potwierdzenie, jak prawidłowo przejść zgłoszenie.
2. Potwierdź kategorie, do których sprzedawca ma prawo publikacji. Sprawdź, czy samo utworzenie i zwykła aktualizacja w wybranych kategoriach nie obciążają konta, pakietu lub płatności odroczonej. Partner API nie dokumentuje uniwersalnej metody gwarantowanego kosztorysu zapisu przed publikacją. Jeśli nie masz potwierdzenia, pozostaw zapis wyłączony.
3. Przygotuj odrębny publiczny adres HTTPS do callbacku i ograniczonych obrazów. Na reverse proxy osiągającym sieć aplikacji HA przepuszczaj tylko `GET /olx/callback` i `GET /olx/media/*` na `http://NAZWA-APLIKACJI:8101`. Pozostałe ścieżki mają zwracać 404. Odłącz logowanie query string i cache na tych ścieżkach. Panel 8099 i MCP 8100 nie są częścią tej konfiguracji.

## Po akceptacji OLX

1. Na karcie **Konfiguracja** aplikacji ustaw `olx_public_base` na adres HTTPS samej domeny, uruchom ponownie i zarejestruj dokładny URL `https://<domena>/olx/callback` w portalu OLX.
2. W **Ogłoszenia → Integracje** podaj identyfikator i sekret aktywnej aplikacji OLX. Kliknij **Połącz z OLX**, zaloguj się po stronie OLX i wyraź zgodę. Po powrocie odśwież ustawienia. Aplikacja wymienia `authorization_code` na token użytkownika i sprawdza `GET /users/me`. `client_credentials` jest trybem aplikacyjnym i nie ma uprawnień do publikacji za użytkownika.
3. Odczytaj kategorię, jej atrybuty, miasto oraz dzielnicę w **Integracje → Dane kategorii i lokalizacji z OLX**. Wpisz właściwe identyfikatory w szkicu. Lokalizację tekstową wpisz tak, jak zwraca ją OLX. Do listy **olx_allowed_categories** wpisz tylko potwierdzone końcowe ID z odpowiednimi uprawnieniami. Ustaw `olx_no_charge_confirmed=true` wyłącznie po potwierdzeniu opisanym wyżej.
4. Jeżeli wszystko jest zweryfikowane ustaw `olx_write_enabled=true`, zrestartuj i przygotuj kompletny podgląd. Po zatwierdzeniu dopiero osobne **Opublikuj w OLX** albo `publish_approved(mode="olx")` podejmuje próbę. Zaakceptowane są tylko kategorie, dla których aplikacja potrafi zweryfikować dane. v1 obsługuje odbiór osobisty w ścieżce OLX. Konfiguracje wysyłki zachowują się w lokalnym szkicu.
5. Użyj **Sprawdź status OLX** i sprawdź stan `active` oraz link zwrócony przez OLX. Statusy moderacji, limitu i wymaganej płatności są pokazywane. Aplikacja nie kupuje pakietów, nie aktywuje płatnych ofert i nie wznawia automatycznie ogłoszeń.
6. Zmiana ceny aktywnego ogłoszenia tworzy w systemie nową wersję do zatwierdzenia. Po ponownej zgodzie zapis aktualizuje istniejący identyfikator przez PUT. **Zakończ ofertę jako sprzedaną** jest osobno potwierdzanym poleceniem właściciela i używa udokumentowanej komendy `deactivate`. Przy niejednoznacznej odpowiedzi odczytaj status OLX.

Aplikacja stosuje `POST /adverts`, `GET /adverts?external_id=…`, `GET/PUT /adverts/{id}` oraz `POST /adverts/{id}/commands`. Nie dołącza płatnych funkcji ani automatycznego przedłużenia. Natychmiastowa rotacja sekretu OLX wymaga ponownego połączenia w panelu. Rozłączenie lokalne nie unieważnia uprawnień po stronie Partner API. Można je cofnąć po stronie OLX.

## Krótkie zapytanie do OLX

> Dzień dobry, używam prywatnego konta OLX Polska i tworzę aplikację do wystawiania wyłącznie własnych ogłoszeń. Czy mogę uzyskać dostęp do Partner API bez firmy i NIP? Na początek chodzi o używane oryginalne części samochodowe, a w przyszłości o elektronikę, narzędzia i wyposażenie domu. Proszę o informację, które kategorie dopuszczają publikację przez API, czy samo utworzenie lub aktualizacja może naliczyć opłatę albo wykorzystać pakiet oraz w jaki sposób potwierdzić zero kosztów przed zapisem.

Źródła. [Dostęp](https://developer.olx.pl/docs/getting-access-to-api) · [OAuth](https://developer.olx.pl/docs/authorization-in-api) · [FAQ](https://developer.olx.pl/docs/faq) · [Specyfikacja OpenAPI v2](https://developer.olx.pl/swagger/v2/partner_api.yaml).
