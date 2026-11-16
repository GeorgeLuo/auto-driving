"""Deferred calls retain JSON history while plugin instances remain replaceable."""

import inspect
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from autonomy.decision_cycle.runner import StepRunner
from autonomy.decision_cycle.plugin_state import unchanged_instance
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
from implementations.decision_cycle.shared.deferred_call import DeferredCall, DeferredCallFailed
from scripts.validation.plugin_state import retained_state

KEY = "proposal.example.call"
MODULE = "implementations.decision_cycle.shared.deferred_call"


class ExamplePlugin:
    plugin_id = "example"

    def __init__(self, run, *, blocking=False):
        self._call = DeferredCall(run, key=KEY, blocking=blocking)

    def propose(self, shared_memory, now, scope, about):
        return self._call.poll(shared_memory, now_ms=now, scope=scope, about=about, request=lambda: about)


class OwnedCall(DeferredCall):
    """Unit calls use the runner boundary; completions have an observable barrier."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.completed = threading.Condition()
        self.done = set()

    def _complete(self, call_id, payload):
        super()._complete(call_id, payload)
        with self.completed:
            self.done.add(call_id)
            self.completed.notify_all()

    def wait_completed(self, count):
        with self.completed:
            if not self.completed.wait_for(lambda: len(self.done) >= count, timeout=3):
                raise AssertionError("worker did not finish")

    def ask(self, memory, **kwargs):
        with StepRunner._plugin_call(SimpleNamespace(step="proposal"), SimpleNamespace(plugin_id="example"), memory):
            return super().ask(memory, **kwargs)

    def take(self, memory, **kwargs):
        with StepRunner._plugin_call(SimpleNamespace(step="proposal"), SimpleNamespace(plugin_id="example"), memory):
            return super().take(memory, **kwargs)

    def drop_stale(self, memory, **kwargs):
        with StepRunner._plugin_call(SimpleNamespace(step="proposal"), SimpleNamespace(plugin_id="example"), memory):
            return super().drop_stale(memory, **kwargs)


class DeferredCallTests(unittest.TestCase):
    def setUp(self):
        self.memory = {}

    def call(self, run, **kwargs):
        call = OwnedCall(run, key=KEY, **kwargs)
        self.addCleanup(call.shutdown)
        return call

    def poll(self, call, *, scope=0, about="first", now=100, request=None):
        return call.poll(self.memory, now_ms=now, scope=scope, about=about,
                         request=(lambda: about) if request is None else request)

    def settled(self, call):
        current = call.record(self.memory)["current"]
        with call.completed:
            self.assertTrue(call.completed.wait_for(lambda: current["call_id"] in call.done, timeout=3))
        call.drop_stale(self.memory, scope=current["scope"])

    def test_background_takes_once_then_asks_about_next_input(self):
        entered, release = threading.Event(), threading.Event()

        def run(request):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test did not release call")
            return {"input": request}

        call = self.call(run)
        self.addCleanup(release.set)
        self.assertIsNone(self.poll(call))
        self.assertTrue(entered.wait(3))
        self.assertIsNone(call.take(self.memory, scope=0))
        release.set()
        self.settled(call)
        answer = self.poll(call, about=None)
        self.assertEqual(answer.result, {"input": "first"})
        self.assertIsNone(call.take(self.memory, scope=0))
        call.ask(self.memory, now_ms=200, scope=0, about="next", request=lambda: "next")
        self.settled(call)
        self.assertEqual(call.take(self.memory, scope=0).result, {"input": "next"})
        self.assertEqual(call.record(self.memory)["counts"]["asked"], 2)

    def test_blocking_poll_answers_now_and_measures_duration_separately(self):
        call = self.call(lambda request: {"input": request}, blocking=True)
        with patch(f"{MODULE}.monotonic", side_effect=[10.0, 10.125]):
            answer = self.poll(call, now=123)
        self.assertEqual((answer.asked_ms, answer.elapsed_ms), (123, 125))
        self.assertEqual(answer.result, {"input": "first"})
        self.assertIsNone(call.take(self.memory, scope=0))

    def test_request_is_lazy_and_pair_is_not_repeated_after_take_or_round_trip(self):
        call = self.call(lambda request: "answer", blocking=True)
        request = Mock(return_value=object())  # payload need not be JSON and must not be retained
        self.assertIsNone(self.poll(call, about=None, request=request))
        request.assert_not_called()
        call.ask(self.memory, now_ms=100, scope=("epoch", 0), about=("input", 1), request=request)
        call.ask(self.memory, now_ms=101, scope=["epoch", 0], about=["input", 2], request=request)
        self.assertEqual(request.call_count, 1)  # an answer is still waiting
        self.assertEqual(call.take(self.memory, scope=["epoch", 0]).result, "answer")
        restored = shared_memory_from_document(shared_memory_document(self.memory))
        fresh = self.call(lambda request: "another", blocking=True)
        fresh.ask(restored, now_ms=200, scope=["epoch", 0], about=["input", 1], request=request)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(fresh.record(restored)["counts"]["asked"], 1)

    def test_new_scope_discards_finished_answer_and_reasks_same_input_in_poll(self):
        call = self.call(lambda request: request, blocking=True)
        call.ask(self.memory, now_ms=1, scope=0, about="same", request=lambda: "old")
        answer = self.poll(call, scope=1, about="same", request=lambda: "new")
        self.assertEqual((answer.scope, answer.result), (1, "new"))
        self.assertEqual(call.record(self.memory)["counts"],
                         {"asked": 2, "answered": 2, "failed": 0, "dropped": 1, "abandoned": 0, "expired": 0})

    def test_drop_stale_counts_other_scopes_and_keeps_this_scopes_answer(self):
        call = self.call(lambda request: request, blocking=True)
        call.ask(self.memory, now_ms=1, scope=0, about="kept", request=lambda: "kept")
        call.drop_stale(self.memory, scope=0)
        self.assertEqual(call.take(self.memory, scope=0).result, "kept")
        call.ask(self.memory, now_ms=2, scope=0, about="stale", request=lambda: "stale")
        call.drop_stale(self.memory, scope=1)
        record = call.record(self.memory)
        self.assertIsNone(record["current"])
        self.assertEqual(record["counts"]["dropped"], 1)

    def test_new_scope_asks_without_waiting_and_late_answer_counts_once(self):
        old_entered, old_release, new_entered, new_release = [threading.Event() for _ in range(4)]

        def run(request):
            entered, release = (old_entered, old_release) if request == "old" else (new_entered, new_release)
            entered.set()
            if not release.wait(3):
                raise AssertionError("test did not release call")
            return request

        call = self.call(run, workers=2)
        self.addCleanup(old_release.set)
        self.addCleanup(new_release.set)
        self.assertIsNone(self.poll(call, request=lambda: "old"))
        self.assertTrue(old_entered.wait(3))
        self.assertIsNone(self.poll(call, scope=1, request=lambda: "new"))
        self.assertTrue(new_entered.wait(3))
        self.assertEqual(call.record(self.memory)["counts"]["dropped"], 0)
        new_release.set()
        self.settled(call)
        old_release.set()
        call.wait_completed(2)
        call.drop_stale(self.memory, scope=1)
        self.assertEqual(call.take(self.memory, scope=1).result, "new")
        self.assertIsNone(call.take(self.memory, scope=1))
        self.assertEqual(call.record(self.memory)["counts"]["answered"], 1)

    def test_hung_call_expires_at_max_age_and_same_pair_is_asked_again(self):
        hung, release = threading.Event(), threading.Event()

        def run(request):
            if request == "hung":
                hung.set()
                if not release.wait(3):
                    raise AssertionError("test did not release call")
            return request

        call = self.call(run, workers=2, max_age_ms=500)
        self.addCleanup(release.set)
        self.assertIsNone(self.poll(call, now=100, request=lambda: "hung"))
        self.assertTrue(hung.wait(3))
        self.assertIsNone(self.poll(call, now=599, request=lambda: "unused"))  # younger than max_age_ms
        self.assertEqual(call.record(self.memory)["counts"]["asked"], 1)
        self.assertIsNone(self.poll(call, now=600, request=lambda: "fresh"))  # same pair, asked anew
        self.settled(call)
        self.assertEqual(call.take(self.memory, scope=0).result, "fresh")
        release.set()
        call.wait_completed(2)
        call.drop_stale(self.memory, scope=0)
        self.assertEqual(call.record(self.memory)["counts"],
                         {"asked": 2, "answered": 1, "failed": 0, "dropped": 1, "abandoned": 0, "expired": 1})


    def test_expiry_bounds_submitted_work_and_retries_after_capacity_returns(self):
        entered, release = threading.Event(), threading.Event()

        def run(request):
            entered.set()
            self.assertTrue(release.wait(3))
            return request

        call = self.call(run, workers=1, max_age_ms=1)
        self.addCleanup(release.set)
        request = Mock(return_value="first")
        self.assertIsNone(self.poll(call, now=0, request=request))
        self.assertTrue(entered.wait(3))
        for now in range(1, 20):
            self.assertIsNone(self.poll(call, now=now, request=request))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(call.record(self.memory)["counts"]["asked"], 1)
        release.set()
        call.wait_completed(1)
        self.poll(call, now=20, request=request)
        call.wait_completed(2)
        self.assertEqual(call.take(self.memory, scope=0).result, "first")
        self.assertEqual(call.record(self.memory)["counts"]["dropped"], 1)
        self.assertEqual(call.record(self.memory)["counts"]["expired"], 1)

    def test_commit_requires_owning_map_and_thread_even_with_copied_context(self):
        from contextvars import copy_context
        from autonomy.shared_memory import require_plugin_call
        call = DeferredCall(lambda request: request, key=KEY, blocking=True)
        self.addCleanup(call.shutdown)
        with self.assertRaisesRegex(RuntimeError, "owning plugin call"):
            call.ask(self.memory, now_ms=0, scope=0, about=1, request=lambda: 1)
        failures = []
        with StepRunner._plugin_call(SimpleNamespace(step="proposal"), SimpleNamespace(plugin_id="example"), self.memory):
            with self.assertRaisesRegex(RuntimeError, "owning plugin call"):
                call.ask({}, now_ms=0, scope=0, about=1, request=lambda: 1)
            copied = copy_context()
            def worker():
                try:
                    copied.run(require_plugin_call, self.memory, KEY)
                except RuntimeError as exc:
                    failures.append(str(exc))
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.memory, {})

    def test_max_age_must_be_a_positive_int(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeferredCall(lambda request: request, key=KEY, blocking=True, max_age_ms=value)

    def test_failure_clears_before_raising_and_same_pair_can_retry(self):
        run = Mock(side_effect=[ValueError("failed input"), {"ok": True}])
        call = self.call(run, blocking=True)
        with self.assertRaisesRegex(DeferredCallFailed, "ValueError: failed input"):
            self.poll(call)
        self.assertIsNone(call.record(self.memory)["current"])
        self.assertEqual(self.poll(call).result, {"ok": True})
        self.assertEqual(call.record(self.memory)["counts"]["failed"], 1)

    def test_loaded_pending_record_is_abandoned_and_same_pair_is_reasked(self):
        entered, release = threading.Event(), threading.Event()

        def run(request):
            entered.set()
            release.wait(3)
            return request

        original = self.call(run)
        self.addCleanup(release.set)
        self.assertIsNone(self.poll(original))
        self.assertTrue(entered.wait(3))
        restored = shared_memory_from_document(shared_memory_document(self.memory))
        fresh = self.call(lambda request: "replacement", blocking=True)
        answer = fresh.poll(restored, now_ms=200, scope=0, about="first", request=lambda: None)
        self.assertEqual(answer.result, "replacement")
        self.assertEqual(fresh.record(restored)["counts"]["abandoned"], 1)
        self.assertEqual(fresh.record(restored)["counts"]["asked"], 2)
        release.set()
        self.settled(original)
        self.assertIsNone(fresh.record(restored)["current"])

    def test_take_then_ask_uses_scope_changed_by_answer_without_dropping(self):
        call = self.call(lambda request: {"scope": request}, blocking=True)
        self.assertIsNone(call.take(self.memory, scope=0))
        call.ask(self.memory, now_ms=10, scope=0, about="same", request=lambda: 1)
        answer = call.take(self.memory, scope=0)
        scope = answer.result["scope"]
        call.ask(self.memory, now_ms=20, scope=scope, about="same", request=lambda: 2)
        self.assertEqual(call.record(self.memory)["current"]["scope"], 1)
        self.assertEqual(call.take(self.memory, scope=1).result, {"scope": 2})
        self.assertEqual(call.record(self.memory)["counts"]["dropped"], 0)

    def test_ask_abandons_replaced_resource_and_late_failure_only_counts_a_drop(self):
        entered, release = threading.Event(), threading.Event()

        def run(request):
            entered.set()
            release.wait(3)
            raise ValueError("obsolete failure")

        original = self.call(run)
        self.addCleanup(release.set)
        self.poll(original)
        self.assertTrue(entered.wait(3))
        replacement = self.call(lambda request: "new", blocking=True)
        replacement.ask(self.memory, now_ms=200, scope=0, about="first", request=lambda: None)
        self.assertEqual(replacement.record(self.memory)["counts"]["abandoned"], 1)
        release.set()
        original.shutdown()
        self.assertEqual(replacement.take(self.memory, scope=0).result, "new")
        self.assertEqual(replacement.record(self.memory)["counts"],
                         {"asked": 2, "answered": 1, "failed": 0, "dropped": 0, "abandoned": 1, "expired": 0})

    def test_plugin_passes_runtime_guard_and_static_state_check(self):
        plugin = ExamplePlugin(lambda request: request, blocking=True)
        self.addCleanup(plugin._call.shutdown)
        for now, about in enumerate(("first", "first", "second")):
            with StepRunner._plugin_call(SimpleNamespace(step="proposal"), plugin, self.memory), unchanged_instance(plugin):
                answer = plugin.propose(self.memory, now, 0, about)
            self.assertEqual(None if answer is None else answer.result, None if now == 1 else about)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plugin.py"
            source.write_text(inspect.getsource(ExamplePlugin))
            self.assertEqual(retained_state(Path(directory), [source.name]), [])

    def test_record_is_detached_and_non_json_result_fails_without_polluting_memory(self):
        call = self.call(lambda request: object(), blocking=True)
        with self.assertRaisesRegex(DeferredCallFailed, "JSON"):
            self.poll(call)
        record = call.record(self.memory)
        record["counts"]["asked"] = 99
        self.assertEqual(call.record(self.memory)["counts"]["asked"], 1)
        shared_memory_from_document(shared_memory_document(self.memory))
        for scope, about in ((object(), 0), (0, float("nan")), ({1: "bad key"}, 0)):
            with self.subTest(scope=scope, about=about), self.assertRaises(TypeError):
                self.poll(call, scope=scope, about=about)

    def test_late_completion_never_recreates_a_removed_key(self):
        entered, release = threading.Event(), threading.Event()

        def run(request):
            entered.set()
            release.wait(3)
            return request

        call = self.call(run)
        self.addCleanup(release.set)
        self.poll(call)
        self.assertTrue(entered.wait(3))
        del self.memory[KEY]
        release.set()
        call.shutdown()
        self.assertIsNone(call.take(self.memory, scope=0))
        self.assertNotIn(KEY, self.memory)
