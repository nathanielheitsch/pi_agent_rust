#!/usr/bin/env python3
"""Disk-safety tests: no Rust build, real disk filling, or artifact deletion."""

import contextlib
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import json
import select
import unittest
from unittest import mock

import cargo_disk_guard as guard


class DiskGuardTests(unittest.TestCase):
    def test_default_limits_keep_substantial_disk_headroom(self):
        self.assertEqual(guard.MIN_FREE, 32 * guard.GIB)
        self.assertEqual(guard.MAX_CACHE, 64 * guard.GIB)

    def test_all_current_and_redirected_cache_roots_are_monitored(self):
        with mock.patch.dict(os.environ, {"CARGO_BUILD_BUILD_DIR": "/separate/build-cache"}, clear=True):
            roots = guard.cache_roots(["cargo", "build", "--target-dir=/other/artifacts"])
        self.assertIn(guard.ROOT / "target", roots)
        self.assertIn(Path("/separate/build-cache"), roots)
        self.assertIn(Path("/other/artifacts"), roots)

    def test_new_and_legacy_cargo_layouts_do_not_double_count(self):
        outputs = [
            guard.ROOT / "target/debug/build/pi_agent_rust/123/out",
            guard.ROOT / "target/debug/build/pi_agent_rust-123",
            guard.ROOT / "target/debug/deps",
        ]
        with mock.patch.dict(os.environ, {}, clear=True):
            for output in outputs:
                with self.subTest(output=output):
                    self.assertEqual(guard.cache_roots(["rustc", "--out-dir", str(output)]),
                                     [guard.ROOT / "target"])

    def test_external_compiler_output_mount_is_monitored(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            roots = guard.cache_roots(["rustc", "--out-dir", "/external/cache/debug/build/pi/hash/out"])
        self.assertIn(Path("/external/cache/debug"), roots)

    def test_ancestor_named_build_does_not_expand_scan_to_a_volume(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            roots = guard.cache_roots(["rustc", "--out-dir", "/Volumes/build/cache/debug/deps"])
        self.assertIn(Path("/Volumes/build/cache/debug"), roots)
        self.assertNotIn(Path("/Volumes"), roots)

    def test_low_space_refuses_before_starting_command(self):
        storage = mock.Mock()
        storage.check.side_effect = guard.DiskPressure("low disk")
        with mock.patch.object(guard.subprocess, "Popen") as launch, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.supervise(["not-started"], storage), guard.REFUSED)
        launch.assert_not_called()

    def test_unreadable_storage_fails_closed(self):
        storage = mock.Mock()
        storage.check.side_effect = PermissionError("cannot measure")
        with mock.patch.object(guard.subprocess, "Popen") as launch, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.supervise(["not-started"], storage), guard.REFUSED)
        launch.assert_not_called()

    def test_storage_reserve_uses_disk_usage_not_platform_df_columns(self):
        with mock.patch.object(guard.shutil, "disk_usage", return_value=mock.Mock(free=guard.MIN_FREE - 1)):
            with self.assertRaises(guard.DiskPressure):
                guard.Storage(["cargo", "build"]).check()

    def test_cache_budget_refuses_even_with_plenty_of_free_disk(self):
        with mock.patch.object(guard.shutil, "disk_usage", return_value=mock.Mock(free=200 * guard.GIB)), \
             mock.patch.object(guard, "allocated_bytes", return_value=guard.MAX_CACHE):
            with self.assertRaisesRegex(guard.DiskPressure, "budget"):
                guard.Storage(["cargo", "build"]).check()

    def test_free_space_is_checked_between_throttled_cache_scans(self):
        with mock.patch.object(guard.shutil, "disk_usage", return_value=mock.Mock(free=200 * guard.GIB)) as disk, \
             mock.patch.object(guard, "allocated_bytes", return_value=0) as size, \
             mock.patch.object(guard.time, "monotonic", return_value=100):
            storage = guard.Storage(["cargo", "build"])
            storage.check()
            storage.check()
            self.assertEqual(size.call_count, 1)
            self.assertEqual(disk.call_count, 2 * len(storage.paths))

    def test_tmpdir_is_also_checked(self):
        with mock.patch.object(guard.tempfile, "gettempdir", return_value="/separate/tmp"):
            self.assertIn(Path("/separate/tmp"), guard.Storage(["cargo", "build"]).paths)

    def test_success_and_failure_exit_codes_are_preserved(self):
        for code in (0, 7):
            with self.subTest(code=code):
                self.assertEqual(guard.supervise([sys.executable, "-c", f"raise SystemExit({code})"],
                                                mock.Mock()), code)

    def test_pressure_during_build_stops_only_the_started_process(self):
        storage = mock.Mock()
        storage.check.side_effect = [None, guard.DiskPressure("disk shrinking")]
        child = mock.Mock(pid=54321)
        child.wait.side_effect = subprocess.TimeoutExpired("compiler", 0.01)
        table = {os.getpid(): (1, 1, "now"), 54321: (os.getpid(), 54321, "child-start")}
        with mock.patch.object(guard, "process_table", return_value=table), \
             mock.patch.object(guard.subprocess, "Popen", return_value=child), \
             mock.patch.object(guard, "stop_group") as stop, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.supervise(["compiler"], storage), guard.REFUSED)
        stop.assert_called_once_with(child)

    @unittest.skipUnless(os.name == "posix", "POSIX process group test")
    def test_live_pressure_terminates_and_reaps_a_real_child(self):
        storage = mock.Mock()
        storage.check.side_effect = [None, guard.DiskPressure("disk shrinking")]
        launch = subprocess.Popen
        children = []

        def capture(*args, **kwargs):
            child = launch(*args, **kwargs)
            if args[0][0] == sys.executable:
                children.append(child)
            return child

        with mock.patch.object(guard.subprocess, "Popen", side_effect=capture), \
             mock.patch.object(guard, "INTERVAL", 0.02), \
             contextlib.redirect_stderr(io.StringIO()):
            code = guard.supervise([sys.executable, "-c", "import time; time.sleep(30)"], storage)
        self.assertEqual(code, guard.REFUSED)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(children[0].pid, 0)

    @unittest.skipUnless(os.name == "posix", "POSIX process group test")
    def test_stop_signals_the_owned_group_not_cargo_globally(self):
        child = mock.Mock(pid=54321, _guard_identity="now")
        table = {54321: (1, 54321, "now"), 9876: (1, 9876, "other")}
        with mock.patch.object(guard, "process_table", side_effect=[table, {}]), \
             mock.patch.object(guard.os, "getsid", side_effect=lambda pid: pid), \
             mock.patch.object(guard.os, "kill") as kill:
            guard.stop_group(child)
        kill.assert_called_once_with(54321, signal.SIGTERM)
        child.wait.assert_called_once()

    def test_nested_compiler_stays_in_outer_supervised_group(self):
        with mock.patch.object(guard, "supervisor_roots", return_value=[guard.ROOT / "target", Path("/output")]), \
             mock.patch.object(sys, "argv", ["guard", "rustc", "--out-dir", "/output"]), \
             mock.patch.object(guard, "require_covered_output"), \
             mock.patch.object(guard, "Storage") as storage, \
             mock.patch.object(guard.os, "execvp", side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit):
                guard.main()
        storage.return_value.check.assert_called_once_with(scan_cache=False)
        execute.assert_called_once_with("rustc", ["rustc", "--out-dir", "/output"])

    def test_unregistered_nested_output_is_refused_before_compiler_exec(self):
        with mock.patch.object(guard, "supervisor_roots", return_value=[guard.ROOT / "target"]), \
             mock.patch.object(sys, "argv", ["guard", "rustc", "--out-dir", "/external/cache/debug/deps"]), \
             mock.patch.object(guard.os, "execvp") as execute, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(), guard.REFUSED)
        execute.assert_not_called()

    def test_nested_mount_inside_known_root_is_refused_unless_device_is_monitored(self):
        root = guard.ROOT / "target"
        path = root / "mounted-volume/debug"
        for devices, allowed in (([11], False), ([11, 22], True)):
            with self.subTest(devices=devices), \
                 mock.patch.dict(os.environ, {"PI_CARGO_DISK_GUARD_DEVICES": json.dumps(devices)}), \
                 mock.patch.object(guard, "existing_parent", return_value=mock.Mock(stat=lambda: mock.Mock(st_dev=22))):
                if allowed:
                    guard.require_covered_output(path, [root])
                else:
                    with self.assertRaisesRegex(guard.DiskPressure, "filesystem"):
                        guard.require_covered_output(path, [root])

    def test_main_checks_actual_output_mount_before_coalescing_cache_roots(self):
        root = guard.ROOT / "target"
        output = root / "debug/deps"

        def parent(path):
            return mock.Mock(stat=lambda: mock.Mock(st_dev=22 if path == output else 11))

        with mock.patch.object(guard, "supervisor_roots", return_value=[root]), \
             mock.patch.dict(os.environ, {"PI_CARGO_DISK_GUARD_DEVICES": "[11]"}), \
             mock.patch.object(sys, "argv", ["guard", "rustc", "--out-dir", str(output)]), \
             mock.patch.object(guard, "existing_parent", side_effect=parent), \
             mock.patch.object(guard.os, "execvp", side_effect=SystemExit(99)) as execute, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(), guard.REFUSED)
        execute.assert_not_called()

    def test_gate_sets_effective_tmpdir_before_monitoring_and_preserves_it(self):
        source = (guard.ROOT / "scripts/verify_local.sh").read_text()
        self.assertLess(source.index('export TMPDIR="${TMPDIR:-/tmp}"'), source.index('--is-supervised'))
        self.assertNotIn("TMPDIR=/tmp", source)

    @unittest.skipUnless(os.name == "posix", "POSIX PID ownership")
    def test_reused_root_and_its_descendants_are_never_adopted(self):
        child = mock.Mock(pid=54321, _guard_identity="original")
        replacement = {54321: (1, 54321, "replacement"), 9876: (54321, 54321, "unrelated")}
        with mock.patch.object(guard, "process_table", return_value=replacement), \
             mock.patch.object(guard.os, "getsid", return_value=54321), \
             mock.patch.object(guard.os, "kill") as kill:
            guard.stop_group(child)
        kill.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX PID ownership")
    def test_root_reused_during_cleanup_cannot_enroll_new_descendants(self):
        child = mock.Mock(pid=54321, _guard_identity="original")
        original = {54321: (1, 54321, "original"), 555: (54321, 54321, "owned-child")}
        replacement = {54321: (1, 54321, "replacement"), 9876: (54321, 54321, "unrelated")}
        with mock.patch.object(guard, "process_table", side_effect=[original, replacement]), \
             mock.patch.object(guard.os, "getsid", return_value=54321), \
             mock.patch.object(guard.os, "kill") as kill:
            guard.stop_group(child)
        self.assertEqual(kill.call_args_list,
                         [mock.call(555, signal.SIGTERM), mock.call(54321, signal.SIGTERM)])

    @unittest.skipUnless(os.name == "posix", "POSIX PID ownership")
    def test_enumeration_failure_never_falls_back_to_an_unvalidated_group(self):
        child = mock.Mock(pid=54321, _guard_identity="original")
        with mock.patch.object(guard, "process_table", side_effect=OSError("ps unavailable")), \
             mock.patch.object(guard.os, "killpg") as kill_group:
            guard.stop_group(child)
        child.kill.assert_called_once()
        kill_group.assert_not_called()


    def test_stale_marker_is_cleared_before_new_supervision(self):
        with mock.patch.dict(os.environ, {"PI_CARGO_DISK_GUARD_PID": "stale"}), \
             mock.patch.object(sys, "argv", ["guard", "command"]), \
             mock.patch.object(guard, "supervise", return_value=0) as run:
            self.assertEqual(guard.main(), 0)
            self.assertNotIn("PI_CARGO_DISK_GUARD_PID", os.environ)
        run.assert_called_once_with(["command"])

    def test_reused_or_unrelated_pid_does_not_validate_a_marker(self):
        marker = {"PI_CARGO_DISK_GUARD_PID": "54321",
                  "PI_CARGO_DISK_GUARD_START": "original",
                  "PI_CARGO_DISK_GUARD_ROOTS": json.dumps([str(guard.ROOT / "target")])}
        for table in ({}, {54321: (1, 54321, "reused")}, {54321: (1, 54321, "original")}):
            with self.subTest(table=table), mock.patch.dict(os.environ, marker), \
                 mock.patch.object(guard, "process_table", return_value=table):
                self.assertIsNone(guard.supervisor_roots())

    @unittest.skipUnless(os.name == "posix", "POSIX ancestry validation")
    def test_real_nested_supervisor_and_stale_marker_recovery(self):
        script = str(Path(guard.__file__).resolve())
        for marker in ("", "stale", "999999999"):
            with self.subTest(marker=marker):
                env = dict(os.environ, PI_CARGO_DISK_GUARD_PID=marker)
                result = subprocess.run(
                    [sys.executable, script, "--", sys.executable, script, "--",
                     sys.executable, "-c", "raise SystemExit(7)"],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 7, result.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX separate-process-group cleanup")
    def test_real_separate_group_descendant_stops_but_unrelated_process_survives(self):
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)
        worker_code = "import time; time.sleep(30)"
        parent_code = (
            "import os,signal,subprocess,sys,time; "
            f"worker=subprocess.Popen([sys.executable,'-c',{worker_code!r}],process_group=0); "
            "signal.signal(signal.SIGTERM,lambda *_: (worker.wait(timeout=5),sys.exit(0))); "
            f"os.write({write_fd},str(worker.pid).encode()); time.sleep(30)"
        )
        calls = 0
        worker_pid = None

        def check():
            nonlocal calls, worker_pid
            calls += 1
            if calls == 1:
                return
            self.assertTrue(select.select([read_fd], [], [], 5)[0], "worker did not become ready")
            worker_pid = int(os.read(read_fd, 128))
            raise guard.DiskPressure("synthetic pressure after readiness")

        storage = mock.Mock()
        storage.check.side_effect = check
        outsider = subprocess.Popen([sys.executable, "-c", worker_code], start_new_session=True)
        try:
            with mock.patch.object(guard, "INTERVAL", 0.05), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = guard.supervise([sys.executable, "-c", parent_code], storage)
            self.assertEqual(code, guard.REFUSED)
            self.assertIsNone(outsider.poll())
            with self.assertRaises(ProcessLookupError):
                os.kill(worker_pid, 0)
        finally:
            outsider.terminate()
            outsider.wait(timeout=5)
            os.close(read_fd)
            os.close(write_fd)

    @unittest.skipUnless(os.name == "posix", "Cargo jobserver uses Unix descriptors")
    def test_cargo_jobserver_descriptors_reach_the_real_compiler(self):
        read_fd, write_fd = os.pipe()
        try:
            os.set_inheritable(read_fd, True)
            os.set_inheritable(write_fd, True)
            code = f"import os; os.fstat({read_fd}); os.fstat({write_fd})"
            self.assertEqual(guard.supervise([sys.executable, "-c", code], mock.Mock()), 0)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_allocated_blocks_deduplicate_hardlinks(self):
        info = mock.Mock(st_mode=stat_mode_file(), st_dev=1, st_ino=5, st_size=4096, st_blocks=8)
        with mock.patch.object(Path, "lstat", return_value=info):
            self.assertEqual(guard.allocated_bytes([Path("/one"), Path("/two")]), 4096)

    def test_internal_symlinks_are_not_followed(self):
        import stat
        info = mock.Mock(st_mode=stat.S_IFLNK, st_dev=1, st_ino=5, st_size=4096, st_blocks=8)
        with mock.patch.object(Path, "lstat", return_value=info), \
             mock.patch.object(guard.os, "scandir") as scan:
            self.assertEqual(guard.allocated_bytes([Path("/link")]), 0)
        scan.assert_not_called()

    def test_disappearing_intermediates_do_not_break_live_scan(self):
        with mock.patch.object(Path, "lstat", side_effect=FileNotFoundError):
            self.assertEqual(guard.allocated_bytes([Path("/removed-by-cargo")]), 0)


def stat_mode_file():
    import stat
    return stat.S_IFREG


if __name__ == "__main__":
    unittest.main(verbosity=2)
