import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import database
from updates import get_updates_text, is_updates_trigger


ROOT = Path(__file__).resolve().parent


class UpdatesTriggerTests(unittest.TestCase):
    def test_reserved_phrase_reads_updates_without_ai(self):
        self.assertTrue(is_updates_trigger("dime tu color favorito"))
        self.assertTrue(is_updates_trigger("Dime tu color favorito!"))
        self.assertFalse(is_updates_trigger("dime tu color favorito de verdad"))
        self.assertIn("ULTIMAS ACTUALIZACIONES DE OSIRIS", get_updates_text())


class DatabaseCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_url = database.DATABASE_URL
        self.old_path = database.DB_PATH
        database.DATABASE_URL = None
        database.DB_PATH = str(Path(self.temp_dir.name) / "osiris-test.db")
        database.init_db()

    def tearDown(self):
        database.DATABASE_URL = self.old_url
        database.DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_reminder_delivery_and_snooze_lifecycle(self):
        reminder_id = database.add_reminder(7, "Tomar agua", "2099-01-31 10:00")
        database.mark_delivery_attempt(reminder_id, "telegram temporal")
        row = database.get_reminder_by_id(reminder_id, 7)
        self.assertEqual(row[10], "retrying")
        self.assertEqual(row[11], 1)

        self.assertTrue(database.snooze_reminder(reminder_id, 7, "2099-01-31 10:10"))
        row = database.get_reminder_by_id(reminder_id, 7)
        self.assertEqual(row[3], "2099-01-31 10:10")
        self.assertEqual(row[10], "pending")
        self.assertEqual(row[11], 0)

        database.mark_delivered(reminder_id)
        self.assertEqual(database.get_reminder_by_id(reminder_id, 7)[10], "sent")

    def test_memory_confirmation_and_contacts(self):
        import features

        database.remember(7, "medico", "Dra. Vargas")
        self.assertEqual(database.get_memories(7, "Vargas")[0][1], "Dra. Vargas")

        token = database.create_pending_action(7, "record_expense", {"amount": 25})
        pending, status = database.consume_pending_action(token, 7)
        self.assertIsNotNone(pending)
        action, payload = pending
        self.assertEqual(action, "record_expense")
        self.assertEqual(payload["amount"], 25)
        pending, status = database.consume_pending_action(token, 7)
        self.assertIsNone(pending)
        self.assertEqual(status, "ya_usado")

        database.save_contact(7, "Dani", 99)
        features.set_preference(7, "voice_replies", True)
        features.add_document(7, "privado.txt", "txt", None, "dato temporal")
        self.assertEqual(database.get_contact(7, "dani"), ("Dani", 99))

        exported = database.export_user_data(7)
        self.assertEqual(exported["memories"][0][1], "Dra. Vargas")
        self.assertEqual(exported["contacts"][0][0], "Dani")

        database.delete_user_data(7)
        self.assertEqual(database.get_memories(7), [])
        self.assertEqual(database.get_contacts(7), [])
        self.assertEqual(features.get_preferences(7), {})
        self.assertEqual(features.list_documents(7), [])

    def test_expense_pdf_supports_multiple_currencies(self):
        import pdf_generator

        database.add_expense(7, 5000, "Supermercado", "comida", "CRC")
        database.add_expense(7, 12, "Hosting", "servicios", "USD")
        with patch.object(pdf_generator, "PDF_DIR", self.temp_dir.name):
            path, message = pdf_generator.generate_expense_report(7, "all")

        pdf_path = Path(path)
        self.assertTrue(pdf_path.exists())
        self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF"))
        self.assertIn("2 gastos", message)

    def test_weekly_pdf_combines_activity_finance_habits_and_goals(self):
        import features
        import pdf_generator

        database.add_expense(7, 1500, "Cafe", "comida", "CRC")
        database.log_activity(7, "crear_recordatorio", "Prueba")
        features.create_habit(7, "Caminar")
        features.log_habit(7, "Caminar")
        features.create_goal(7, "Meta semanal")
        with patch.object(pdf_generator, "PDF_DIR", self.temp_dir.name):
            path, message = pdf_generator.generate_weekly_report(7)
        self.assertTrue(Path(path).read_bytes().startswith(b"%PDF"))
        self.assertIn("semanal", message.lower())

    def test_preferences_flags_history_and_undo(self):
        import features

        features.set_preference(7, "private_mode", True)
        self.assertTrue(features.get_preference(7, "private_mode"))
        features.set_feature_flag(7, "documents", False)
        self.assertFalse(features.feature_enabled(7, "documents"))
        self.assertTrue(features.feature_enabled(7, "inbox"))

        item_id = features.add_inbox_item(7, "Idea reversible", "ideas")
        self.assertEqual(features.get_inbox(7)[0][0], item_id)
        self.assertEqual(features.undo_last_action(7), "capture_inbox")
        self.assertEqual(features.get_inbox(7), [])

    def test_planning_routines_habits_goals_and_dates(self):
        import features

        routine_id = features.create_routine(7, "manana", ["Tomar agua", "Revisar agenda"])
        routine = features.get_routine(7, "manana")
        self.assertEqual(routine[0], routine_id)
        self.assertEqual(len(routine[2]), 2)

        features.create_habit(7, "Leer", "daily", 1)
        self.assertIsNotNone(features.log_habit(7, "Leer"))
        self.assertEqual(features.get_habits(7)[0][4], 1)

        goal_id = features.create_goal(7, "Aprender Python", steps=["Curso", "Proyecto"])
        self.assertEqual(features.get_goals(7)[0][0], goal_id)
        self.assertEqual(features.get_goals(7)[0][5], 2)

        event_date = (database.local_now() + timedelta(days=3)).strftime("%Y-%m-%d")
        features.add_important_date(7, "Fecha de prueba", event_date, recurring=False)
        self.assertEqual(features.get_upcoming_dates(7, 7)[0][-1], 3)

    def test_document_library_chunks_searches_and_deletes(self):
        import features

        content = ("Osiris conserva el conocimiento importante. " * 80).strip()
        document_id, created = features.add_document(
            7, "manual.txt", "txt", "telegram-file-id", content
        )
        self.assertTrue(created)
        self.assertGreater(len(features.list_documents(7)), 0)
        matches = features.search_documents(7, "conocimiento importante")
        self.assertEqual(matches[0][0], "manual.txt")
        duplicate_id, duplicate_created = features.add_document(
            7, "manual-copia.txt", "txt", "otro-file-id", content
        )
        self.assertEqual(duplicate_id, document_id)
        self.assertFalse(duplicate_created)
        self.assertTrue(features.delete_document(7, document_id))
        self.assertEqual(features.list_documents(7), [])

    def test_budgets_duplicates_subscriptions_and_expense_items(self):
        import features

        features.set_budget(7, "comida", "CRC", 10000, 80)
        expense_id = database.add_expense(7, 8500, "Super", "comida", "CRC")
        features.add_expense_items(
            expense_id,
            [{"description": "Arroz", "quantity": 2, "unit_price": 1000, "total": 2000}],
        )
        status = features.get_budget_status(7)[0]
        self.assertEqual(status[4], 8500)
        self.assertIsNotNone(features.find_duplicate_expense(7, 8500, "Super", "CRC"))

        subscription_id = features.add_subscription(
            7, "Servicio", 10, "USD", "2027-01-31", "monthly"
        )
        self.assertEqual(features.advance_subscription(7, subscription_id), "2027-02-28")
        self.assertEqual(features.get_subscriptions(7)[0][0], subscription_id)
        self.assertIn("current", features.get_monthly_expense_comparison(7))
        self.assertEqual(len(features.get_expense_export_rows(7)), 1)

    def test_shared_task_lists_are_visible_to_member(self):
        import features

        list_id = database.create_task_list(7, "Compras compartidas")
        database.add_task_item(list_id, "Leche")
        features.share_resource(7, "task_list", list_id, 99, "edit")
        self.assertEqual(database.search_lists(99, "Compras")[0][0], list_id)
        shared = features.get_shared_resources(99)
        self.assertEqual(shared[0][2], list_id)
        self.assertFalse(database.is_task_list_owner(99, list_id))

    def test_meeting_minutes_provider_usage_and_cache(self):
        import features

        meeting_id = features.start_meeting(7, "Proyecto Osiris")
        self.assertEqual(features.get_active_meeting(7)[0], meeting_id)
        self.assertIsNotNone(features.add_meeting_item(7, "Publicar beta", "decision"))
        ended = features.end_meeting(7)
        self.assertEqual(ended[0], meeting_id)
        self.assertEqual(ended[2][0][0], "decision")
        self.assertIsNone(features.get_active_meeting(7))
        self.assertEqual(features.set_meeting_summary(7, meeting_id, "Minuta final"), 1)

        features.record_provider_usage("groq", "test", "ok", 7)
        features.cache_response("public-search:test", "respuesta", ttl_minutes=5)
        self.assertEqual(features.get_cached_response("public-search:test"), "respuesta")

    def test_encrypted_backup_restores_relations_atomically(self):
        import os
        import backup_tools
        import features

        list_id = database.create_task_list(7, "Respaldo")
        database.add_task_item(list_id, "Elemento original")
        features.add_document(7, "notas.txt", "txt", None, "contenido importante")
        features.create_habit(7, "Respirar")
        with patch.dict(os.environ, {"OSIRIS_BACKUP_KEY": "clave-de-prueba-segura"}):
            blob, filename, count = backup_tools.create_encrypted_backup(7)
            self.assertTrue(blob.startswith(backup_tools.BACKUP_HEADER))
            self.assertTrue(filename.endswith(".osirisbackup"))
            self.assertGreater(count, 0)
            payload = backup_tools.decrypt_backup(blob, 7)
            with self.assertRaises(ValueError):
                backup_tools.decrypt_backup(blob, 99)

            database.add_task_item(list_id, "No debe sobrevivir")
            restored = backup_tools.restore_backup_payload(payload, 7)

        self.assertGreater(restored, 0)
        restored_lists = database.search_lists(7, "Respaldo")
        self.assertEqual(len(restored_lists), 1)
        restored_items = database.get_list_items(restored_lists[0][0])
        self.assertEqual([row[1] for row in restored_items], ["Elemento original"])
        self.assertEqual(features.list_documents(7)[0][1], "notas.txt")
        self.assertEqual(features.get_habits(7)[0][1], "Respirar")

    def test_google_revocation_removes_local_token(self):
        import os
        import auth

        with patch.dict(os.environ, {"GOOGLE_TOKEN_ENCRYPTION_KEY": "clave-google-prueba"}):
            token = auth._encrypt_token('{"token":"access","refresh_token":"refresh"}')
            database.save_token(7, token)
            response = SimpleNamespace(status_code=200)
            with patch.object(auth.requests, "post", return_value=response) as post:
                existed, remote = auth.revoke_google_access(7)
        self.assertTrue(existed)
        self.assertTrue(remote)
        self.assertIsNone(database.get_token(7))
        post.assert_called_once()


class BotCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_delivery_is_retried(self):
        import bot

        fake_job_queue = SimpleNamespace(run_once=Mock())
        context = SimpleNamespace(
            job=SimpleNamespace(data={
                "rid": 5,
                "uid": 7,
                "text": "Prueba",
                "recurring": None,
                "dt_str": "2099-01-01 10:00",
                "search_query": None,
                "friend_name": None,
                "end_date": None,
                "lead_minutes": 0,
                "attempts": 0,
            }),
            bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("temporal"))),
            job_queue=fake_job_queue,
        )
        reminder_row = (5, 7, "Prueba", "2099-01-01 10:00", None, None, None, None, 0, 1, "pending", 0)
        with patch.object(bot, "get_reminder_by_id", return_value=reminder_row), patch.object(bot, "mark_delivery_attempt") as mark_attempt:
            await bot.send_reminder(context)
        mark_attempt.assert_called_once()
        fake_job_queue.run_once.assert_called_once()

    def test_monthly_recurrence_clamps_to_last_day(self):
        import bot

        current = bot.parse_local("2027-01-31 09:00")
        self.assertEqual(bot.calc_next(current, "monthly").strftime("%Y-%m-%d"), "2027-02-28")

    def test_conversation_context_is_explicit(self):
        import inspect
        import bot

        parameters = inspect.signature(bot.process_action).parameters
        self.assertIn("history", parameters)
        self.assertIn("memories", parameters)

    def test_amount_parser_supports_local_formats(self):
        import bot

        self.assertEqual(bot.parse_amount("12.500,00"), 12500.0)
        self.assertEqual(bot.parse_amount("12,500.00"), 12500.0)
        self.assertEqual(bot.parse_amount("12,500"), 12500.0)

    async def test_expense_summary_handles_multiple_currencies(self):
        import bot

        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        expenses = [
            (5000.0, "super_mercado", "comida", "CRC"),
            (12.0, "hosting [mensual]", "servicios", "USD"),
        ]
        with (
            patch.object(bot, "get_today_expenses", return_value=expenses),
            patch.object(bot, "save_exchange") as save_exchange,
        ):
            await bot.process_action(
                update,
                SimpleNamespace(),
                "cuanto gaste hoy",
                {"action": "expense_summary"},
                7,
            )

        reply = message.reply_text.await_args.args[0]
        self.assertIn("5000 CRC", reply)
        self.assertIn("12 USD", reply)
        save_exchange.assert_called_once()

    async def test_research_pdf_summarizes_sources_before_rendering(self):
        import os
        import bot

        message = SimpleNamespace(reply_text=AsyncMock(), reply_document=AsyncMock())
        update = SimpleNamespace(message=message)
        sources = [{"title": "Fuente", "body": "Dato verificado", "href": "https://example.com"}]
        report = {
            "summary": "Resumen verificado.",
            "key_points": ["Dato concreto."],
            "limitations": "Corte temporal.",
        }
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
            temp.write(b"%PDF-test")
            temp_path = temp.name
        try:
            with (
                patch.object(bot, "research_results", return_value=sources),
                patch.object(bot, "summarize_research", return_value=report),
                patch.object(bot, "generate_text_pdf", return_value=temp_path) as generate_pdf,
                patch.object(bot, "log_activity"),
                patch.object(bot, "save_exchange"),
            ):
                await bot.process_action(
                    update,
                    SimpleNamespace(),
                    "dame un pdf con un resumen del mundial 2026",
                    {
                        "action": "generate_pdf",
                        "type": "content",
                        "query": "resumen mundial 2026",
                        "title": "Resumen del Mundial 2026",
                    },
                    7,
                )

            generate_pdf.assert_called_once_with(
                "Resumen del Mundial 2026",
                report,
                "informe",
                "resumen mundial 2026",
                sources,
            )
            message.reply_document.assert_awaited_once()
            self.assertFalse(os.path.exists(temp_path))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class ConfigurationRegressionTests(unittest.TestCase):
    def test_google_oauth_is_scoped_and_not_oob(self):
        source = (ROOT / "auth.py").read_text(encoding="utf-8")
        self.assertNotIn("urn:ietf:wg:oauth:2.0:oob", source)
        self.assertNotIn("save_token(0", source)
        self.assertNotIn("get_token(0", source)
        self.assertIn("save_token(user_id", source)
        self.assertIn("gmail.compose", source)

    def test_google_tokens_are_encrypted(self):
        import os
        import auth

        with patch.dict(os.environ, {"GOOGLE_TOKEN_ENCRYPTION_KEY": "prueba-segura"}):
            encrypted = auth._encrypt_token('{"token":"secreto"}')
            self.assertTrue(encrypted.startswith("enc:"))
            self.assertNotIn("secreto", encrypted)
            self.assertEqual(auth._decrypt_token(encrypted), '{"token":"secreto"}')

    def test_free_router_and_production_initialization(self):
        ai_source = (ROOT / "ai_handler.py").read_text(encoding="utf-8")
        bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('"openrouter/free"', ai_source)
        self.assertIn("loop.run_until_complete(post_init(ptb_app))", bot_source)
        self.assertIn('context.user_data.pop("music_pending", False)', bot_source)

    def test_ai_prompt_formats_with_memory(self):
        import ai_handler

        prompt = ai_handler.SYSTEM_PROMPT.format(
            current_date="2099-01-01",
            current_time="12:00",
            timezone="America/Costa_Rica",
            history="",
            memories="- nombre: Yecso",
        )
        self.assertIn("nombre: Yecso", prompt)

    def test_ai_falls_back_when_openrouter_returns_null_content(self):
        import os
        import ai_handler

        openrouter_client = Mock()
        openrouter_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
        )
        groq_client = Mock()
        groq_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"action":"chat","message":"ok"}'))]
        )

        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test", "GROQ_API_KEY": "groq-test", "GOOGLE_API_KEY": ""}),
            patch.object(ai_handler, "OpenAI", return_value=openrouter_client),
            patch.object(ai_handler, "Groq", return_value=groq_client),
            patch.object(ai_handler, "_record_provider") as record_provider,
        ):
            content = ai_handler._call_ai([{"role": "user", "content": "hola"}])

        self.assertEqual(content, '{"action":"chat","message":"ok"}')
        self.assertEqual(record_provider.call_args_list[0].args, ("openrouter", "chat", "error"))
        self.assertEqual(record_provider.call_args_list[1].args, ("groq", "chat", "ok"))

    def test_analyze_message_handles_null_content(self):
        import ai_handler

        with patch.object(ai_handler, "_call_ai", return_value=None):
            result = ai_handler.analyze_message("recuerdame llamar manana")

        self.assertEqual(result["action"], "chat")
        self.assertIn("repetirlo", result["message"])

    def test_groq_fallback_compacts_large_system_prompt(self):
        import ai_handler

        prompt = ai_handler.SYSTEM_PROMPT.format(
            current_date="2099-01-01",
            current_time="12:00",
            timezone="America/Costa_Rica",
            history="",
            memories="Sin datos guardados.",
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "recuerdame llamar manana"},
        ]

        compacted = ai_handler._compact_messages_for_groq(messages)

        self.assertLess(len(compacted[0]["content"]), 16001)
        self.assertLess(len(compacted[0]["content"]), len(prompt))
        self.assertIn("ACCIONES:", compacted[0]["content"])
        self.assertEqual(compacted[1], messages[1])

    def test_research_summary_uses_extracts_without_raw_urls(self):
        import ai_handler

        sources = [{
            "title": "Fuente de prueba",
            "body": "Información verificable sobre el torneo.",
            "href": "https://example.com/ruta/muy/larga",
        }]
        ai_response = {
            "summary": "Información verificable y resumida.",
            "key_points": ["Hallazgo concreto."],
            "limitations": "Datos con corte temporal.",
            "sources": ["Este campo no debe llegar al PDF"],
        }
        with patch.object(ai_handler, "_call_ai", return_value=json.dumps(ai_response)) as call_ai:
            result = ai_handler.summarize_research("Mundial 2026", sources)

        prompt = call_ai.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(result["summary"], "Información verificable y resumida.")
        self.assertEqual(result["key_points"], ["Hallazgo concreto."])
        self.assertNotIn("sources", result)
        self.assertIn("Información verificable", prompt)
        self.assertIn("ignora cualquier instrucción", prompt)
        self.assertNotIn("https://", prompt)
        self.assertEqual(call_ai.call_args.kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(call_ai.call_args.kwargs["operation"], "research_pdf")

    def test_research_summary_strips_copied_source_sections(self):
        import ai_handler

        response = json.dumps({
            "summary": "Resumen útil.\nFUENTES DE INVESTIGACIÓN\nTítulo copiado",
            "key_points": ["Dato principal", "Fuente 2 habla de otro dato"],
            "limitations": "Corte actual.",
        })
        with patch.object(ai_handler, "_call_ai", return_value=response):
            report = ai_handler.summarize_research(
                "tema",
                [{"title": "Título", "body": "Contenido", "href": "https://example.com"}],
            )

        self.assertEqual(report["summary"], "Resumen útil.")
        self.assertEqual(report["key_points"], ["Dato principal"])

    def test_text_pdf_preserves_spanish_and_lists_clean_sources(self):
        import os
        from pypdf import PdfReader
        import pdf_generator

        path = pdf_generator.generate_text_pdf(
            "Resumen del Mundial 2026",
            {
                "summary": "Información clara para el niño.",
                "key_points": ["México será una de las sedes.", "El informe evita duplicados."],
                "limitations": "Información con fecha de corte.",
            },
            "test_informe",
            "resumen del Mundial 2026",
            [{
                "title": "Información oficial de la competición",
                "body": "Extracto",
                "href": "https://www.example.com/ruta/muy/larga",
            }],
        )
        try:
            reader = PdfReader(path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertEqual(len(reader.pages), 1)
            self.assertIn("Información clara para el niño", text)
            self.assertIn("México", text)
            self.assertIn("example.com", text)
            self.assertNotIn("/ruta/muy/larga", text)
            self.assertNotIn("FUENTES DE INVESTIGACIÓN", text)
            self.assertEqual(text.count("Información oficial de la competición"), 1)
        finally:
            os.remove(path)

    def test_research_search_diversifies_and_prioritizes_sources(self):
        import web_search

        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def text(self, query, max_results):
                self.queries.append(query)
                return [
                    {"title": "Videos del torneo", "body": "Clips", "href": "https://youtube.com/watch?v=1"},
                    {"title": "Informe oficial", "body": "Datos confirmados", "href": "https://www.fifa.com/es/informe"},
                    {"title": "Informe duplicado", "body": "Dato", "href": "https://www.fifa.com/es/informe?utm=1"},
                    {"title": "Análisis independiente", "body": "Contexto", "href": "https://example.org/analisis"},
                ]

            def news(self, query, max_results):
                self.news_queries.append(query)
                return [{
                    "title": "Resultado reciente",
                    "body": "Marcador y contexto confirmado",
                    "url": "https://news.example.com/resultado",
                    "date": "2026-07-19",
                }]

            queries = []
            news_queries = []

        FakeDDGS.queries = []
        FakeDDGS.news_queries = []
        with patch.object(web_search, "DDGS", FakeDDGS):
            results = web_search.research_results("Mundial 2026", max_results=3)

        self.assertEqual(len(FakeDDGS.queries), 3)
        self.assertEqual(len(FakeDDGS.news_queries), 1)
        self.assertIn("semifinales", FakeDDGS.queries[0])
        self.assertEqual(results[0]["title"], "Informe oficial")
        self.assertEqual(sum("fifa.com" in item["href"] for item in results), 1)

    def test_research_pdf_stays_on_one_page_at_content_limits(self):
        import os
        from pypdf import PdfReader
        import pdf_generator

        report = {
            "summary": ("Resumen detallado del acontecimiento con datos verificados y contexto. " * 12)[:600],
            "key_points": [
                (f"Hallazgo {index} con una explicación concreta, fecha, resultado y contexto relevante. " * 3)[:150]
                for index in range(1, 6)
            ],
            "limitations": ("Información disponible hasta la fecha de corte; algunos datos pueden cambiar. " * 3)[:180],
        }
        sources = [
            {
                "title": f"Fuente informativa número {index} con un título suficientemente descriptivo",
                "body": "Extracto",
                "href": f"https://fuente{index}.example.com/informe",
            }
            for index in range(1, 7)
        ]
        path = pdf_generator.generate_text_pdf(
            "Informe de actualidad",
            report,
            "test_limites",
            "tema de prueba",
            sources,
        )
        try:
            self.assertEqual(len(PdfReader(path).pages), 1)
        finally:
            os.remove(path)


class DashboardCoreTests(unittest.TestCase):
    def setUp(self):
        import dashboard

        self.dashboard = dashboard
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_url = database.DATABASE_URL
        self.old_path = database.DB_PATH
        self.old_password = dashboard.PASSWORD
        self.old_ema_api_key = dashboard.EMA_API_KEY
        database.DATABASE_URL = None
        database.DB_PATH = str(Path(self.temp_dir.name) / "dashboard-test.db")
        dashboard.DATABASE_URL = None
        dashboard.PASSWORD = "test-password"
        dashboard.EMA_API_KEY = "test-ema-key"
        dashboard._ema_answer_cache.clear()
        dashboard.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        database.init_db()
        self.client = dashboard.app.test_client()

    def tearDown(self):
        database.DATABASE_URL = self.old_url
        database.DB_PATH = self.old_path
        self.dashboard.PASSWORD = self.old_password
        self.dashboard.EMA_API_KEY = self.old_ema_api_key
        self.temp_dir.cleanup()

    def test_health_and_session_login(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 302)
        response = self.client.post("/login", data={"password": "test-password"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_ema_chat_requires_bearer_token_and_returns_sources(self):
        self.assertEqual(self.client.post("/api/v1/ema/chat", json={"message": "hola"}).status_code, 401)
        sources = [{
            "title": "Calendario oficial",
            "body": "Saprissa juega a las 20:00.",
            "href": "https://example.com/partido",
            "date": "2026-07-23",
        }]
        with patch.object(self.dashboard, "search_results", return_value=sources), patch.object(
            self.dashboard,
            "summarize_research",
            return_value={
                "summary": "Saprissa juega hoy a las 20:00.",
                "key_points": ["El encuentro es nocturno."],
                "limitations": "El horario puede cambiar.",
            },
        ):
            response = self.client.post(
                "/api/v1/ema/chat",
                json={"message": "¿A qué hora juega Saprissa hoy?"},
                headers={"Authorization": "Bearer test-ema-key", "X-EMA-Device": "test-device"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Saprissa juega hoy a las 20:00.", response.get_json()["answer"])
        self.assertIn("Puntos clave:", response.get_json()["answer"])
        self.assertIn("Alcance:", response.get_json()["answer"])
        self.assertEqual(response.get_json()["sources"][0]["url"], "https://example.com/partido")

    def test_ema_chat_caches_repeated_questions(self):
        sources = [{
            "title": "Dragon Ball",
            "body": "Goku es el protagonista.",
            "href": "https://example.com/goku",
            "date": "",
        }]
        report = {
            "summary": "Goku es un personaje de Dragon Ball.",
            "key_points": ["Es un saiyajin."],
            "limitations": "",
        }
        headers = {"Authorization": "Bearer test-ema-key", "X-EMA-Device": "cache-device"}
        with patch.object(self.dashboard, "search_results", return_value=sources) as search, patch.object(
            self.dashboard,
            "summarize_research",
            return_value=report,
        ) as summarize:
            first = self.client.post("/api/v1/ema/chat", json={"message": "Quien es Goku?"}, headers=headers)
            second = self.client.post("/api/v1/ema/chat", json={"message": "Quien es Goku?"}, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json(), second.get_json())
        search.assert_called_once_with("Quien es Goku?", max_results=3)
        summarize.assert_called_once_with(
            "Quien es Goku?",
            sources,
            fast=True,
            prefer_google=True,
        )


if __name__ == "__main__":
    unittest.main()
