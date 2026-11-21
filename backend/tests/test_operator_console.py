import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


CONSOLE_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "console"


class OperatorConsoleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_console_page_and_assets_are_served(self) -> None:
        page = self.client.get("/console")
        stylesheet = self.client.get("/console/assets/styles.css")
        script = self.client.get("/console/assets/app.js")

        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("안전 작업 콘솔", page.text)
        self.assertIn("/console/assets/styles.css", page.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("text/css", stylesheet.headers["content-type"])
        self.assertEqual(script.status_code, 200)
        self.assertIn("javascript", script.headers["content-type"])

    def test_console_uses_real_backend_workflow_endpoints(self) -> None:
        script = (CONSOLE_DIR / "app.js").read_text(encoding="utf-8")
        for path in (
            "/task-orders/preview",
            "/task-orders/from-natural-language",
            "/action-proposals/",
            "/execute",
            "/execution-logs",
        ):
            self.assertIn(path, script)

    def test_visible_assets_do_not_contain_forbidden_dash_characters(self) -> None:
        for path in CONSOLE_DIR.iterdir():
            if path.suffix not in {".html", ".css", ".js"}:
                continue
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("—", contents, path.name)
            self.assertNotIn("–", contents, path.name)


if __name__ == "__main__":
    unittest.main()
