"""Launcher readiness, browser opening and cleanup; no real browsers or servers are started."""
import json
import unittest
from unittest.mock import Mock, call, patch

import run


class LauncherTests(unittest.TestCase):
    def test_browser_opens_only_after_api_and_screen_are_ready(self):
        events = []
        processes = [Mock(), Mock()]
        with patch.object(run, "service_up", return_value=False), \
                patch.object(run.subprocess, "Popen", side_effect=processes), \
                patch.object(run, "wait_for_service", side_effect=lambda name, proc: events.append(name) or True), \
                patch.object(run.webbrowser, "open_new_tab", side_effect=lambda url: events.append(url) or True):
            self.assertEqual(run.start(["api", "inbox"], open_browser=True), processes)
        self.assertEqual(events, ["api", "inbox", "http://localhost:8502"])

    def test_existing_services_are_opened_without_duplicate_processes(self):
        with patch.object(run, "service_up", return_value=True), \
                patch.object(run.subprocess, "Popen") as spawn, \
                patch.object(run.webbrowser, "open_new_tab") as browser:
            self.assertEqual(run.start(["api", "chat"], open_browser=True), [])
            spawn.assert_not_called()
            browser.assert_called_once_with("http://localhost:8501")

    def test_failed_start_cleans_up_owned_processes_and_does_not_open_browser(self):
        process = Mock()
        with patch.object(run, "service_up", return_value=False), \
                patch.object(run.subprocess, "Popen", return_value=process), \
                patch.object(run, "wait_for_service", return_value=False), \
                patch.object(run, "stop") as stop, \
                patch.object(run.webbrowser, "open_new_tab") as browser:
            with self.assertRaises(RuntimeError):
                run.start(["api", "chat"], open_browser=True)
            stop.assert_called_once_with([process])
            browser.assert_not_called()

    def test_failed_child_is_detected_without_waiting_for_timeout(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(run, "service_up") as health, patch.object(run.time, "sleep") as sleep:
            self.assertFalse(run.wait_for_service("chat", process))
            health.assert_not_called()
            sleep.assert_not_called()

    def test_editor_play_button_defaults_to_everything_and_browser(self):
        with patch.object(run, "start", return_value=[]) as start:
            self.assertEqual(run.main([]), 0)
            start.assert_called_once_with(["api", "chat", "inbox", "scope", "dashboard"], open_browser=True)

    def test_each_task_starts_its_api_and_opens_its_screen(self):
        with patch.object(run, "start", return_value=[]) as start:
            for task in ("task1", "task2", "task3", "dashboard", "api"):
                self.assertEqual(run.main([task, "--open-browser"]), 0)
            self.assertEqual(start.call_args_list, [
                call(["api", "chat"], open_browser=True), call(["api", "inbox"], open_browser=True),
                call(["api", "scope"], open_browser=True), call(["dashboard"], open_browser=True),
                call(["api"], open_browser=True)])

    def test_cli_does_not_open_browser_unless_requested(self):
        with patch.object(run, "start", return_value=[]) as start:
            run.main(["task1"])
            start.assert_called_once_with(["api", "chat"], open_browser=False)

    def test_vscode_app_entries_use_runner_and_browser_option(self):
        launch = json.loads((run.ROOT / ".vscode/launch.json").read_text(encoding="utf-8"))
        apps = [c for c in launch["configurations"] if c.get("presentation", {}).get("group") == "1. Run apps"]
        self.assertEqual({c["args"][0] for c in apps}, {"all", "task1", "task2", "task3", "dashboard", "api"})
        for config in apps:
            self.assertEqual(config["program"], "${workspaceFolder}/run.py")
            self.assertIn("--open-browser", config["args"])
            self.assertTrue(config["subProcess"])


if __name__ == "__main__":
    unittest.main()
