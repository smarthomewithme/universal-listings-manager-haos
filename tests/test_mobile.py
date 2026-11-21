import os
import tempfile
import unittest

from common import live_server, sample_bytes

BASE = "http://127.0.0.1:8099"
PREFIX = "/api/hassio_ingress/mobile-test"


@unittest.skipUnless(os.getenv("ULM_BROWSER_TEST") == "1", "Uruchom z ULM_BROWSER_TEST=1 i Playwright.")
class MobilePanelTests(unittest.TestCase):
    def test_mobile_panel_create_upload_review_publish_and_change_price(self):
        from playwright.sync_api import expect, sync_playwright

        with (
            tempfile.TemporaryDirectory() as folder,
            live_server(folder, enabled=False),
            sync_playwright() as pw,
        ):
            browser = pw.chromium.launch(executable_path=os.getenv("ULM_BROWSER_EXECUTABLE") or None)
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            # Use the same path prefix and relative URLs as Ingress while using the real backend.
            page.route(
                BASE + PREFIX + "/**",
                lambda route: route.fulfill(
                    response=route.fetch(url=route.request.url.replace(PREFIX, "", 1))
                ),
            )
            page.goto(BASE + PREFIX + "/")
            expect(page.get_by_role("heading", name="Pulpit")).to_be_visible()
            page.get_by_role("link", name="+ Nowe ogłoszenie").click()
            page.locator("#f-title").fill("Element Audi do testu")
            page.locator("#f-category").select_option("car_parts")
            page.get_by_label("Numer OEM").fill("testowe oznaczenie")
            page.locator("#f-description").fill(
                "Testowy opis części. Stan i oznaczenia zweryfikowane do demonstracji."
            )
            page.locator("#f-price_minor").fill("149,99")
            page.locator("#f-location").fill("Olsztyn")
            page.locator("#f-condition").fill("Używany")
            page.get_by_role("button", name="Utwórz szkic", exact=True).click()
            expect(page.get_by_role("heading", name="Element Audi do testu")).to_be_visible()
            images = [
                {"name": "a.jpg", "mimeType": "image/jpeg", "buffer": sample_bytes("JPEG", "red")},
                {"name": "b.png", "mimeType": "image/png", "buffer": sample_bytes("PNG", "green")},
                {"name": "c.webp", "mimeType": "image/webp", "buffer": sample_bytes("WEBP", "blue")},
            ]
            page.locator("#photos").set_input_files(images)
            page.get_by_role("button", name="Prześlij zdjęcia").click()
            expect(page.locator(".photo")).to_have_count(3)
            expect(page.get_by_text("Zdjęcie główne · a.jpg")).to_be_visible()
            page.locator(".photo").filter(has_text="c.webp").get_by_role("button", name="Główne").click()
            expect(page.get_by_text("Zdjęcie główne · c.webp")).to_be_visible()
            page.locator(".photo").filter(has_text="b.png").get_by_role("button", name="Wyżej").click()
            expect(page.locator(".photo").nth(1)).to_contain_text("b.png")
            page.locator(".photo").filter(has_text="a.jpg").get_by_role("button", name="Usuń").click()
            expect(page.locator(".photo")).to_have_count(2)
            page.locator(".photo").filter(has_text="c.webp").get_by_role("button", name="Obróć").click()
            page.get_by_role("link", name="Podgląd").click()
            expect(page.get_by_role("heading", name="Podgląd ogłoszenia")).to_be_visible()
            expect(page.locator(".gallery img")).to_have_count(2)
            expect(page.get_by_text("testowe oznaczenie", exact=True)).to_be_visible()
            expect(page.locator(".price")).to_contain_text("149,99")
            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            page.set_viewport_size({"width": 320, "height": 700})
            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            page.get_by_role("button", name="Przekaż do zatwierdzenia").click()
            expect(page.get_by_role("button", name="Zatwierdź ogłoszenie")).to_be_visible()
            page.get_by_role("button", name="Zatwierdź ogłoszenie").click()
            expect(page.get_by_text("Zatwierdzone, oczekuje na połączenie OLX")).to_be_visible()
            page.get_by_role("button", name="Symuluj publikację").click()
            expect(page.get_by_role("link", name="Otwórz wynik demonstracyjny")).to_be_visible()
            page.get_by_role("link", name="Otwórz wynik demonstracyjny").click()
            expect(page.get_by_role("heading", name="Wynik demonstracyjny")).to_be_visible()
            page.get_by_role("link", name="Wróć do szkicu").click()
            page.locator("#f-price_minor").fill("150")
            page.get_by_role("button", name="Zapisz zmiany").click()
            expect(page.get_by_text("Zapisano szkic.")).to_be_visible()
            page.get_by_role("link", name="Podgląd").click()
            expect(page.get_by_role("button", name="Przekaż do zatwierdzenia")).to_be_visible()
            expect(page.get_by_role("button", name="Symuluj publikację")).to_have_count(0)

            page.get_by_role("link", name="Pulpit").click()
            page.get_by_role("link", name="Import z ChatGPT").click()
            page.locator("#chatgpt-json").fill(
                '{"fields":{"title":"Lampka demonstracyjna","category":"electronics"},'
                '"suggestion":{"price_minor":8000,"min_minor":6000,"max_minor":10000}}'
            )
            page.get_by_role("button", name="Importuj szkic").click()
            expect(page.get_by_role("heading", name="Lampka demonstracyjna")).to_be_visible()
            page.get_by_role("link", name="Podgląd").click()
            expect(page.get_by_text("szacunek bez źródeł", exact=False)).to_be_visible()
            self.assertEqual(errors, [])
            page.close()
            browser.close()
