# Architektura

Jeden proces Python 3.12 z Uvicornem i SQLite. Panel używa Starlette i statycznego HTML, CSS oraz JavaScript. Serwer `/mcp` używa MCP Python SDK 2.3.0, Streamable HTTP, stateless z odpowiedziami JSON. Brak osobnej usługi AI, bundlera, PostgreSQL, MQTT i Node-RED. SQLite wystarcza dla pojedynczego właściciela oraz wielu klientów odczytujących i edytujących jego szkice.

| Port wewnętrzny | Odbiorca | Dostęp |
|---|---|---|
| 8099 | Panel, REST, zdjęcia, health | Supervisor Ingress i tożsamość właściciela. Health z loopbacku lub Supervisora |
| 8100 | MCP i metadane OAuth | Opcjonalny nasłuch. Prywatny Bearer lub JWT od skonfigurowanego wystawcy |
| 8101 | Callback OLX i kopie zdjęć do OLX | Opcjonalny nasłuch. Jednorazowe OAuth state lub czasowy podpis konkretnego zdjęcia |

HAOS nie mapuje tych portów na host. Plik `/data/options.json` pochodzi z Supervisora. `DEV_PANEL=true` działa wyłącznie poza HAOS i wiąże wszystkie porty z loopbackiem.

## Encje i dane

- **Product** jest niezależnym elementem katalogu. Kilka szkiców może wskazywać jeden produkt. v1 udostępnia platformę OLX i adapter demonstracyjny.
- **ListingDraft** przechowuje pola publiczne, notatki prywatne, platformę, rewizję i stan. Cena to grosze jako liczba całkowita, waluta `PLN`. **CategoryAttributes** obejmują lokalne dynamiczne formularze oraz oddzielne kody i reguły zwracane przez OLX.
- **MediaAsset** ma klucz SHA-256 oryginału i zawiera oryginał oraz miniaturę. Relacja szkic–zdjęcie trzyma kolejność, obrót i nazwę. Pierwsza pozycja jest okładką. Zdjęcia i ich powiązania zapisują się w tej samej transakcji.
- **Approval** zawiera identyfikator, numer rewizji, właściciela, datę wygaśnięcia i SHA-256 kanonicznego JSON z polami publicznymi, kolejnością zaakceptowanych oryginałów i obrotem. Publikacja potwierdza też hashe bajtów. Cofnięcia zostają w historii.
- **MarketplaceListing** trzyma każdą zarezerwowaną próbę i stan zweryfikowany przez adapter. **ProviderAccount** zawiera zaszyfrowany komplet OLX. **AuditEvent** pokazuje kto, co i kiedy. **PriceSuggestion** ma odrębną kwotę, zakres, źródła, pewność, notatkę i datę.
- SQLite używa WAL, kluczy obcych, `BEGIN IMMEDIATE` do zapisu i kontroli oczekiwanej rewizji. Zmiana z nieaktualną rewizją zwraca konflikt. Klucze żądań tworzenia blokują powielanie szkicu przy utracie odpowiedzi. Archiwizacja i usunięcie logiczne zachowują historię.

## Publikowanie

Szkic → do zatwierdzenia → zatwierdzony → osobne polecenie publikacji. Tryb demo zapisuje osobny wynik bez zmiany stanu oferty na aktywny. Prawdziwy zapis rezerwuje próbę przed żądaniem OLX. OLX może przyjąć ofertę i skierować ją do moderacji. Dopiero odczyt stanu `active` oznacza `published`. Retry po utracie odpowiedzi może odpalić wyłącznie odczyt. W szczególności w przypadku aktualizacji z niejednoznacznym wynikiem sam identyfikator OLX nie dowodzi zastosowania nowych pól.

Zatwierdzenie nie uruchamia publikacji automatycznie. Zgodnie z docelowym przepływem osobne wywołanie pochodzi z MCP albo z panelu. Cena aktywnej oferty jest najpierw przygotowywana lokalnie jako nowa rewizja. Zakończenie aktywnej oferty wymaga osobnej akcji właściciela w panelu.
