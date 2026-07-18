import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_source(filename):
    return (ROOT / filename).read_text(encoding="utf-8")


def function_source(filename, function_name):
    source = read_source(filename)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"No se encontro {function_name} en {filename}")


class SecurityRegressionTests(unittest.TestCase):
    def test_webhook_requires_telegram_secret(self):
        webhook = function_source("bot.py", "webhook")
        self.assertIn("X-Telegram-Bot-Api-Secret-Token", webhook)
        self.assertIn("compare_digest", webhook)

        main = function_source("bot.py", "main")
        self.assertIn("secret_token=WEBHOOK_SECRET", main)

    def test_panel_is_creator_only_and_never_sends_password(self):
        panel = function_source("bot.py", "panel")
        self.assertIn("CREATOR_ID", panel)
        self.assertNotIn("DASHBOARD_PASSWORD", panel)
        self.assertNotIn("pwd=", panel)

    def test_photos_use_the_same_authorization_gate(self):
        handler = function_source("bot.py", "handle_photo")
        self.assertRegex(handler, r"if\s+not\s+await\s+check_auth\(")

    def test_dashboard_has_no_default_password_or_password_urls(self):
        dashboard = read_source("dashboard.py")
        bot = read_source("bot.py")
        self.assertNotIn("osiris123", dashboard + bot)
        self.assertIn('@app.route("/login", methods=["GET", "POST"])', dashboard)
        self.assertIn('session.get("authenticated")', dashboard)
        self.assertNotRegex(dashboard, r"pwd=|request\.args\.get\(\"pwd\"\)")

        protected_actions = {
            "task_toggle": '@app.route("/task_toggle/<int:item_id>", methods=["POST"])',
            "task_delete": '@app.route("/task_delete/<int:item_id>", methods=["POST"])',
            "delete": '@app.route("/delete/<int:rid>", methods=["POST"])',
            "logout": '@app.route("/logout", methods=["POST"])',
        }
        for function_name, route in protected_actions.items():
            self.assertIn(route, dashboard)
            self.assertIn("check_csrf", function_source("dashboard.py", function_name))

    def test_documentation_source_contains_no_live_credentials(self):
        docs = read_source("generate_docs.py")
        credential_patterns = (
            r"\d{8,12}:AA[A-Za-z0-9_-]{20,}",
            r"gsk_[A-Za-z0-9]{20,}",
            r"postgres(?:ql)?://[^\s\"]+:[^\s\"]+@",
            r"GOCSPX-[A-Za-z0-9_-]+",
            r"API Key: [a-f0-9]{32}",
        )
        for pattern in credential_patterns:
            self.assertIsNone(re.search(pattern, docs), pattern)

        self.assertFalse(
            (ROOT / "OSIRIS_BOT_DOCUMENTACION.pdf").exists(),
            "El PDF anterior contiene secretos y debe regenerarse despues de rotarlos.",
        )


if __name__ == "__main__":
    unittest.main()
