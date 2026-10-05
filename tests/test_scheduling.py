"""Tests for scheduled emails, reminders, autostart and command follow-ups."""

import http.client
import json
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import main
from agent import Agent
from brain.base import BrainReply, ToolCall
from memory import MemoryStore
from scheduler import Scheduler
from tests.helpers import ScriptedBrain, make_config, temp_dir
from tools.base import Tool, ToolError
from tools.routing import select_tool_names
from ui.server import JarvisWebServer
from utils import autostart
from utils.when import WhenError, describe_time, parse_when

NOW = datetime(2026, 10, 5, 21, 30)  # a Monday evening


class ParseWhenTests(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(parse_when(text, NOW), expected, text)

    def test_common_phrases(self):
        self.check("tomorrow at 6 am", datetime(2026, 10, 6, 6, 0))
        self.check("Tomorrow 6AM", datetime(2026, 10, 6, 6, 0))
        self.check("in 10 minutes", NOW + timedelta(minutes=10))
        self.check("after 2 hours", NOW + timedelta(hours=2))
        self.check("in half an hour", NOW + timedelta(minutes=30))
        self.check("10 mins from now", NOW + timedelta(minutes=10))
        self.check("friday 5:30 pm", datetime(2026, 10, 9, 17, 30))
        self.check("next monday 9am", datetime(2026, 10, 12, 9, 0))
        self.check("12/10 at 7 pm", datetime(2026, 10, 12, 19, 0))
        self.check("12 october", datetime(2026, 10, 12, 9, 0))
        self.check("october 12 2027 at 8:15", datetime(2027, 10, 12, 8, 15))
        self.check("day after tomorrow at noon", datetime(2026, 10, 7, 12, 0))
        self.check("tomorrow evening", datetime(2026, 10, 6, 18, 0))

    def test_evening_words_mean_pm(self):
        self.check("tonight at 11", datetime(2026, 10, 5, 23, 0))
        self.check("tomorrow night at 9", datetime(2026, 10, 6, 21, 0))

    def test_time_alone_is_the_next_time_it_happens(self):
        self.check("at 6", datetime(2026, 10, 6, 6, 0))      # 6:00 already passed today
        self.check("at 22:15", datetime(2026, 10, 5, 22, 15))

    def test_todays_weekday_means_next_week_once_passed(self):
        self.check("monday", datetime(2026, 10, 12, 9, 0))

    def test_past_and_nonsense_are_rejected(self):
        for text in ["whenever", "yesterday at 5", "2 days ago", "today at 8 am", "at 25:00", "13 pm"]:
            with self.assertRaises(WhenError, msg=text):
                parse_when(text, NOW)

    def test_odd_time_spellings(self):
        self.check("10 :7 pm tomorrow", datetime(2026, 10, 6, 22, 7))
        self.check("tomorrow 10.7 pm", datetime(2026, 10, 6, 22, 7))
        self.check("12.10", datetime(2026, 10, 12, 9, 0))  # without am/pm it's still a date

    def test_time_that_just_passed_means_right_away(self):
        # e.g. "send it at 9:25 pm" approved at 9:30: send now, not tomorrow or never.
        self.check("at 9:25 pm", NOW)
        self.check("9:25 pm today", NOW)
        self.check("monday 9:25 pm", NOW)
        self.check("at 9:15 pm", datetime(2026, 10, 6, 21, 15))  # long gone: tomorrow
        with self.assertRaises(WhenError):
            parse_when("today at 9:15 pm", NOW)
        self.assertIn("(right away)", describe_time(NOW, NOW))

    def test_describe_time(self):
        self.assertEqual(describe_time(datetime(2026, 10, 6, 6, 0), NOW), "Tue 6 Oct 2026, 6:00 AM (in 8 hours)")
        self.assertIn("(in 1 minute)", describe_time(NOW + timedelta(minutes=1), NOW))


class FakeSend(Tool):
    """Stands in for send_email: records emails instead of sending them."""

    name = "send_email"
    parameters = {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                   "body": {"type": "string"}}, "required": ["to"]}
    sent = None
    fail = False

    def run(self, to, subject="", body=""):
        if self.fail:
            raise ToolError("The email server rejected the login.")
        self.sent.append((to, subject, body))
        return "Email sent."


def make_agent(tmp, brain=None, email=True):
    extra = {"EMAIL_ADDRESS": "me@example.com", "EMAIL_PASSWORD": "app-password"} if email else {}
    config = make_config(tmp, **extra)
    return Agent(config, brain or ScriptedBrain(), MemoryStore(config.db_path), "You are JARVIS.")


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.agent = make_agent(self.tmp.name)
        self.store = self.agent.memory_store
        self.fake = FakeSend(self.agent.tools.get("send_email").context)
        self.fake.sent = []
        self.agent.tools.register(self.fake, replace=True)
        self.now = NOW
        self.toasts = []
        self.scheduler = Scheduler(self.store, self.agent.tools, clock=lambda: self.now,
                                   toast=lambda title, text: self.toasts.append(title))

    def tearDown(self):
        self.tmp.cleanup()

    def email(self, run_at):
        return self.store.add_task("email", run_at, {"to": "bob@example.com", "subject": "Hi", "body": "Hello"})

    def test_email_is_sent_on_time_then_deleted(self):
        self.email(NOW + timedelta(minutes=5))
        self.assertEqual(self.scheduler.run_due(), 0)
        self.assertEqual(self.fake.sent, [])
        self.now = NOW + timedelta(minutes=5)
        self.assertEqual(self.scheduler.run_due(), 1)
        self.assertEqual(self.fake.sent, [("bob@example.com", "Hi", "Hello")])
        self.assertEqual(self.store.list_tasks(), [])  # nothing left behind in the database
        note = self.scheduler.notifications_since(0)[-1]
        self.assertEqual(note["kind"], "email")
        self.assertIn("bob@example.com", note["text"])
        self.assertEqual(self.toasts, ["Scheduled email sent"])
        self.scheduler.run_due()
        self.assertEqual(len(self.fake.sent), 1)  # never sent twice

    def test_slightly_late_email_is_still_sent(self):
        self.email(NOW - timedelta(hours=3))
        self.scheduler.run_due()
        self.assertEqual(len(self.fake.sent), 1)
        self.assertIn("late", self.scheduler.notifications_since(0)[-1]["text"])

    def test_very_late_email_is_missed_not_sent(self):
        task = self.email(NOW - timedelta(hours=13))
        self.scheduler.run_due()
        self.assertEqual(self.fake.sent, [])
        self.assertEqual(self.store.get_task(task.id).status, "missed")
        self.assertEqual(self.scheduler.notifications_since(0)[-1]["kind"], "error")

    def test_failed_email_is_kept_then_purged(self):
        self.fake.fail = True
        task = self.email(NOW)
        self.scheduler.run_due()
        saved = self.store.get_task(task.id)
        self.assertEqual(saved.status, "failed")
        self.assertIn("rejected", saved.result)
        self.assertIn("FAILED", self.scheduler.notifications_since(0)[-1]["title"])
        self.scheduler.run_due()  # not retried over and over
        self.assertEqual(len(self.scheduler.notifications_since(0)), 1)
        self.now = NOW + timedelta(days=8)
        self.scheduler._last_purge = None
        self.scheduler.run_due()
        self.assertIsNone(self.store.get_task(task.id))

    def test_reminder_notifies_and_is_deleted(self):
        heard = []
        self.scheduler.listeners.append(heard.append)
        self.store.add_task("reminder", NOW, {"message": "check the oven"})
        self.scheduler.run_due()
        self.assertEqual(heard[0]["text"], "check the oven")
        self.assertEqual(heard[0]["kind"], "reminder")
        self.assertEqual(self.store.list_tasks(), [])

    def test_notifications_since(self):
        for n in range(3):
            self.scheduler.notify("reminder", "Reminder", f"r{n}")
        self.assertEqual([n["text"] for n in self.scheduler.notifications_since(1)], ["r1", "r2"])
        self.assertEqual(self.scheduler.last_notification_id, 3)


class ScheduleToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_schedule_email_asks_approval_showing_time_then_saves(self):
        call = ToolCall("schedule_email", {"to": "bob@example.com", "subject": "Report",
                                           "body": "It's ready.", "when": "tomorrow at 6 am"})
        agent = make_agent(self.tmp.name, ScriptedBrain(BrainReply("", [call]), BrainReply("Scheduled.")))
        reply = agent.handle("email bob@example.com tomorrow at 6 am saying the report is ready")
        self.assertIsNotNone(reply.pending)
        self.assertIn("Send at:", reply.pending.description)
        self.assertIn("6:00 AM", reply.pending.description)
        self.assertIn("It's ready.", reply.pending.description)
        self.assertEqual(agent.memory_store.list_tasks(), [])  # nothing saved before approval
        agent.resolve_pending(True)
        tasks = agent.memory_store.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].kind, "email")
        self.assertEqual((tasks[0].run_at.hour, tasks[0].run_at.minute), (6, 0))
        self.assertEqual(tasks[0].payload["to"], "bob@example.com")

    def test_schedule_email_needs_email_setup(self):
        agent = make_agent(self.tmp.name, email=False)
        result = agent.tools.execute("schedule_email", {"to": "a@b.com", "subject": "x", "body": "y",
                                                        "when": "in 5 minutes"})
        self.assertIn("Email is not set up", result)

    def test_bad_time_is_an_error(self):
        agent = make_agent(self.tmp.name)
        result = agent.tools.execute("set_reminder", {"message": "x", "when": "whenever"})
        self.assertTrue(result.startswith("Error:"))

    def test_reminder_list_and_cancel(self):
        agent = make_agent(self.tmp.name)
        self.assertFalse(agent.tools.needs_confirmation("set_reminder", {"message": "x", "when": "in 5 minutes"}))
        result = agent.tools.execute("set_reminder", {"message": "stretch", "when": "in 10 minutes"})
        self.assertIn("Reminder #1", result)
        self.assertIn("stretch", agent.handle("/scheduled").text)
        self.assertIn("Cancelled", agent.handle("/cancel 1").text)
        self.assertIn("Nothing is scheduled", agent.handle("/scheduled").text)
        self.assertIn("no scheduled task #9", agent.handle("/cancel 9").text)

    def test_routing_offers_scheduling_tools_for_times(self):
        names = ["send_email", "schedule_email", "set_reminder", "list_scheduled", "cancel_scheduled", "web_search"]
        for text in ["email bob tomorrow at 6 am", "remind me in 5 minutes", "set a timer", "send it at 7pm"]:
            self.assertIn("schedule_email", select_tool_names(names, text, set(), set()), text)
        self.assertNotIn("set_reminder", select_tool_names(names, "i am happy", set(), set()))


class KnowledgeRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()

    def tearDown(self):
        self.tmp.cleanup()

    def sent_text(self, agent, brain, question):
        agent.handle(question)
        return brain.calls[-1][0][-1]["content"]

    def test_one_shared_common_word_does_not_attach_knowledge(self):
        brain = ScriptedBrain()
        agent = make_agent(self.tmp.name, brain)
        agent.memory_store.add_knowledge("artificial intelligence",
                                         "AI is used today in apps; send data, 10 examples.")
        text = self.sent_text(agent, brain, "send it at 10:10 PM today")
        self.assertNotIn("learned earlier", text)

    def test_clearly_related_knowledge_is_attached(self):
        brain = ScriptedBrain()
        agent = make_agent(self.tmp.name, brain)
        agent.memory_store.add_knowledge("solar panels", "Panels convert sunlight to power.")
        self.assertIn("learned earlier", self.sent_text(agent, brain, "how much do solar panels cost?"))
        self.assertIn("learned earlier", self.sent_text(agent, brain, "does sunlight power them at night?"))


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_commands_are_part_of_the_conversation(self):
        brain = ScriptedBrain(BrainReply("Which one?"))
        agent = make_agent(self.tmp.name, brain)
        agent.memory_store.add_task("reminder", datetime.now() + timedelta(hours=1), {"message": "stretch"})
        agent.handle("/scheduled")
        agent.handle("cancel that one")
        messages, tools = brain.calls[0]
        contents = [m["content"] for m in messages]
        self.assertIn("/scheduled", contents)
        self.assertTrue(any("stretch" in c for c in contents if c != "/scheduled"))
        # "cancel that one" alone has no scheduling words, but follows /scheduled.
        self.assertIn("cancel_scheduled", [t["function"]["name"] for t in tools])

    def test_other_commands_stay_out_of_the_conversation(self):
        agent = make_agent(self.tmp.name)
        agent.handle("/help")
        agent.handle("/status")
        self.assertEqual(len(agent.conversation), 0)

    def test_status_shows_scheduled_and_autostart(self):
        status = make_agent(self.tmp.name).status()
        self.assertEqual(status["Scheduled"], "0 waiting")
        self.assertIn(status["Start with Windows"], ("on", "off"))


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_on_off_writes_and_removes_startup_script(self):
        agent = make_agent(self.tmp.name)
        with mock.patch.object(autostart.sys, "platform", "win32"), \
                mock.patch.dict(autostart.os.environ, {"APPDATA": self.tmp.name}):
            reply = agent.handle("/autostart on").text
            script = autostart.script_path()
            self.assertTrue(script.exists(), reply)
            content = script.read_text(encoding="utf-8")
            self.assertIn("start_jarvis.bat", content)
            self.assertIn("--no-browser", content)
            self.assertIn(", 7, False", content)  # minimized, don't wait
            self.assertIn("ON", agent.handle("/autostart").text)
            self.assertIn("no longer", agent.handle("/autostart off").text)
            self.assertFalse(script.exists())
            self.assertIn("wasn't", agent.handle("/autostart off").text)

    def test_quotes_in_path_are_escaped(self):
        content = autostart.startup_script(Path('C:/odd "name"/jarvis'))
        self.assertIn('""name""', content)

    def test_only_on_windows(self):
        agent = make_agent(self.tmp.name)
        with mock.patch.object(autostart.sys, "platform", "linux"):
            self.assertIn("only available on Windows", agent.handle("/autostart on").text)


class WebNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.agent = make_agent(self.tmp.name)
        self.agent.config.web_port = 0
        self.server = JarvisWebServer(self.agent, self.agent.config)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def get(self, path, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.server.port}"}
        if token:
            headers["X-Jarvis-Token"] = self.server.token
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        body = response.read().decode()
        conn.close()
        return response.status, body

    def test_notifications_endpoint(self):
        self.agent.scheduler.toast = None
        self.agent.scheduler.notify("reminder", "Reminder", "drink water")
        self.agent.scheduler.notify("reminder", "Reminder", "stretch")
        status, body = self.get("/api/notifications?after=1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual([n["text"] for n in data["notifications"]], ["stretch"])
        self.assertEqual(data["last_id"], 2)
        self.assertEqual(self.get("/api/notifications", token=False)[0], 403)

    def test_already_running_detection(self):
        self.assertTrue(main.jarvis_already_running(self.server.port))
        self.assertFalse(main.jarvis_already_running(1))


if __name__ == "__main__":
    unittest.main()
