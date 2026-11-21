# Universal Listings Manager through the existing Home Assistant MCP

This companion integration registers **Universal Listings Manager** with Home Assistant Core's
LLM API. It uses the existing Home Assistant MCP connection for ChatGPT. Core contacts the
ULM app over its internal HAOS network using a separately scoped credential. The same GitHub
repository provides the HAOS app and the HACS integration.

## Installation in Polish

1. W sklepie aplikacji HA zaktualizuj **Universal Listings Manager** do **1.2.0**. Otwórz
   kartę **Konfiguracja** aplikacji. Ustaw `ha_llm_token` na nowy, losowy token o długości co
   najmniej 32 znaków (litery, cyfry, `_` lub `-`). Zapisz i uruchom ponownie aplikację.
   Token możesz wygenerować w menedżerze haseł. Zachowaj go do następnego kroku.
2. W **HACS → ⋮ → Własne repozytoria** dodaj
   `https://github.com/smarthomewithme/universal-listings-manager-haos` jako **Integracja**.
   Pobierz **Universal Listings Manager 1.2.0** i zrestartuj **Home Assistant Core**.
   Wymagany jest Core **2026.11.0** lub nowszy.
3. Otwórz **Ustawienia → Urządzenia i usługi → Dodaj integrację → Universal Listings Manager**.
   Wklej ten sam token. Dla instalacji z podanego URL domyślny host to
   `6a2752e9-olx-manager`. Dla innej instalacji użyj hosta podanego w Informacjach aplikacji.
   Wybierz konto **administratora HA**, pod którym działa istniejące połączenie SHWM MCP.
   Formularz sprawdzi adres, uprawnienia tokenu oraz dostępność wymaganych funkcji.
4. Przejdź do **Ustawienia → System → AI → Model Context Protocol → Główny URL → Konfiguruj**.
   Jeśli masz ręcznie wybrane zestawy narzędzi, zaznacz również **Universal Listings Manager**.
   Zachowaj pozostałe zaznaczenia. Przy opcji **Expose all AI tools** zestaw dołącza automatycznie.
   Na próbę możesz zostawić **Read-only**. Wyłącz tę opcję, jeśli chcesz tworzyć lub edytować
   szkice i symulować publikację przez główny MCP. Ustawienie dotyczy całego głównego endpointu.
5. Odśwież listę narzędzi w istniejącym kliencie SHWM Home Assistant i rozpocznij nową rozmowę,
   jeżeli klient pamięta poprzednią listę. Korzystaj z dotychczasowego połączenia wskazującego
   główny endpoint HA `/api/mcp`. Indywidualny link `/api/mcp/assist` zgodnie z działaniem HA
   zwraca tylko narzędzia sterowania urządzeniami. Jeśli klient używa właśnie tego indywidualnego
   adresu, dołączenie ULM wymaga przełączenia jego istniejącego połączenia na główny endpoint.

W razie problemu w **Urządzenia i usługi → Universal Listings Manager → ⋮** użyj **Zmień konfigurację**
lub pobierz diagnostykę. Raport pokazuje stan połączenia, wersję i zgodność. Nie zawiera tokenu
ani danych ogłoszeń. Po zmianie tokenu w aplikacji wprowadź nowy również w integracji.

> Katalog HACS jest niezależny od sklepu aplikacji HA. ULM to nadal jedna aplikacja na HAOS.
> Komponent dodany przez HACS działa w HA Core i rejestruje narzędzia w istniejącym MCP.
> Pole `mcp_enabled` aplikacji ULM można zostawić wyłączone. Instalacja z własnego repozytorium
> HACS nie wymaga wpisu w centralnym Home Assistant Marketplace.

## Capabilities and boundaries

| Main endpoint | ULM capabilities |
|---|---|
| Read-only | List and read listings, list album, get thumbnail, preview, approval status, last known OLX status and activity log |
| Writes enabled | The above plus create or edit a draft, change price, reorder photos, request owner review and simulate publication |

The selected active admin account is the only HA principal that can list or invoke ULM tools.
Other accounts continue to see the tools allowed by their existing HA configuration. The ULM
app has its own limited internal credential stored in the Supervisor options and in HA's private
integration configuration. Treat Home Assistant backups as sensitive. There are no host port
mappings for the adapter. The full ULM MCP listener and the scoped credential are independent.

Approval and live OLX publication are outside this tool set. The existing owner-only approval
flow, revision checks, idempotency and simulation status are enforced in the app. The central
route cannot invoke the live publish operation, even if OLX write access is enabled in ULM.

The HA Core MCP server in 2026.11 serializes LLM tool results as JSON text. The thumbnail tool
returns a single 256px JPEG as Base64 with media type and dimensions. An MCP client must decode
it to display the image. The ULM panel displays originals and previews; full image upload remains
in the panel. The existing direct ULM MCP can still return native MCP image content.

References: [Home Assistant MCP](https://www.home-assistant.io/integrations/mcp_server/) ·
[HACS custom repositories](https://hacs.dev/docs/faq/custom_repositories/).

## Live acceptance on the owner's HAOS

On the existing SHWM connection, verify `ulm_list_drafts` alongside an existing HA tool.
With Read-only enabled, confirm `ulm_create_draft` is absent. With writes enabled, create a
disposable draft, edit its price, upload a photo in the panel and request review. Verify that
a simulation before approval fails, then approve in the owner panel and simulate it.
Check the local result is `mock_published` and the real OLX state is unchanged.
