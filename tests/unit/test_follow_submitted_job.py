from unittest.mock import MagicMock, patch

import pytest

from app.utils.scheduler import (
    JobCompletionInfo,
    JobOutputFiles,
    follow_submitted_job,
    get_job_completion_info,
    read_job_output_files,
)

_JOB_ID = "12345"

_SQUEUE_RUNNING = (0, ["12345 normal mymodel user PD 0:00 1 (Resources)"])
_SQUEUE_EMPTY = (0, [""])
_SQUEUE_ERROR = (1, ["slurm_load_jobs error: Invalid job id specified"])
_TAIL_OUTPUT = (0, ["line 1", "line 2"])
_SACCT_COMPLETED = (0, [f"{_JOB_ID}|COMPLETED|0:0|00:05:23|4096K"])
_SACCT_ERROR = (1, ["sacct: error: Problem talking to the database"])
_SACCT_NONE = (None, [])

_OUTPUT_STDOUT_ONLY = JobOutputFiles(
    stdout_content="line 1\nline 2\nline 3\n",
    stderr_content=None,
    stdout_exists=True,
    stderr_exists=False,
)
_OUTPUT_MISSING = JobOutputFiles(
    stdout_content=None,
    stderr_content=None,
    stdout_exists=False,
    stderr_exists=False,
)
_OUTPUT_BOTH = JobOutputFiles(
    stdout_content="Model converged\n",
    stderr_content="Warning: deprecated config\n",
    stdout_exists=True,
    stderr_exists=True,
)


class TestReadJobOutputFiles:
    def test_both_files_exist_returns_content(self, tmp_path, monkeypatch):
        """Both files present: content is returned and exists flags are True."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stdout.modelops").write_text("Model converged successfully\n")
        (tmp_path / "stderr.modelops").write_text("Warning: deprecated config\n")

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stderr_exists is True
        assert result.stdout_content == "Model converged successfully\n"
        assert result.stderr_content == "Warning: deprecated config\n"

    def test_only_stdout_exists(self, tmp_path, monkeypatch):
        """Only stdout present: stderr_content is None and stderr_exists is False."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stdout.modelops").write_text("output\n")

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stderr_exists is False
        assert result.stdout_content == "output\n"
        assert result.stderr_content is None

    def test_neither_file_exists(self, tmp_path, monkeypatch):
        """Neither file present: both content fields are None, both exists flags False."""
        monkeypatch.chdir(tmp_path)

        result = read_job_output_files()

        assert result.stdout_exists is False
        assert result.stderr_exists is False
        assert result.stdout_content is None
        assert result.stderr_content is None

    def test_large_stdout_truncated_to_10000_lines(self, tmp_path, monkeypatch):
        """A file with 50000 lines returns only the last 10000 lines."""
        monkeypatch.chdir(tmp_path)
        total_lines = 50_000
        lines = [f"line {i}\n" for i in range(total_lines)]
        (tmp_path / "stdout.modelops").write_text("".join(lines))

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stdout_content is not None
        returned_lines = result.stdout_content.splitlines()
        assert len(returned_lines) == 10_000
        # Last line must be from the end of the file
        assert returned_lines[-1] == f"line {total_lines - 1}"
        # First line in the result must not be "line 0"
        assert returned_lines[0] == f"line {total_lines - 10_000}"

    def test_non_utf8_bytes_handled_with_replace(self, tmp_path, monkeypatch):
        """Files with invalid UTF-8 bytes are read without raising (errors='replace')."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stdout.modelops").write_bytes(b"valid text\xff\xfe invalid bytes\n")

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stdout_content is not None
        # Replacement character inserted, no exception raised
        assert "valid text" in result.stdout_content

    def test_custom_filenames_accepted(self, tmp_path, monkeypatch):
        """Custom stdout_file and stderr_file parameters are used."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "custom_out.log").write_text("custom stdout\n")
        (tmp_path / "custom_err.log").write_text("custom stderr\n")

        result = read_job_output_files(
            stdout_file="custom_out.log",
            stderr_file="custom_err.log",
        )

        assert result.stdout_content == "custom stdout\n"
        assert result.stderr_content == "custom stderr\n"

    def test_permission_error_yields_none_content_exists_true(self, tmp_path, monkeypatch):
        """A file that exists but cannot be read sets content=None without raising."""
        monkeypatch.chdir(tmp_path)
        stdout_path = tmp_path / "stdout.modelops"
        stdout_path.write_text("data\n")

        original_open = open

        def patched_open(path, *args, **kwargs):
            from pathlib import Path as _Path

            if _Path(path).name == "stdout.modelops":
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=patched_open):
            result = read_job_output_files()

        # File exists (Path.exists() succeeds) but open() raises
        assert result.stdout_exists is True
        assert result.stdout_content is None


class TestFastJob:
    def test_stdout_read_after_instant_completion(self, capsys):
        """Job exits before first squeue check; post-completion output is logged."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_EMPTY,       # _job_in_queue → False, loop skipped
                    _SACCT_COMPLETED,    # get_job_completion_info sacct call
                ],
            ) as mock_rit,
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
            patch("app.utils.scheduler.sleep") as mock_sleep,
        ):
            result = follow_submitted_job(_JOB_ID, timeout=60)

        assert result is None
        # sleep must NOT be called (loop never entered)
        mock_sleep.assert_not_called()
        # Two run_in_terminal calls: squeue check + sacct (no cat)
        assert mock_rit.call_count == 2
        captured = capsys.readouterr()
        assert "stdout.modelops" in captured.out

    def test_no_sleep_before_first_squeue_check(self):
        """Verifies sleep is not called before _job_in_queue returns False."""
        call_order: list[str] = []

        def fake_rit(cmds, **kwargs):
            call_order.append("run_in_terminal")
            return _SQUEUE_EMPTY

        def fake_sleep(seconds):
            call_order.append("sleep")

        with (
            patch("app.utils.scheduler.run_in_terminal", side_effect=fake_rit),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_MISSING,
            ),
            patch("app.utils.scheduler.sleep", side_effect=fake_sleep),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        # The first event must be run_in_terminal (the squeue check), never sleep
        assert call_order[0] == "run_in_terminal"
        assert "sleep" not in call_order


class TestNormalJob:
    def test_stdout_tailed_each_iteration(self):
        """For a 3-iteration job, tail must be called 3 times inside the loop."""
        # squeue: running x3, done
        # tail: called 3 times inside the loop
        # sacct: called once after loop exits (no cat call; read_job_output_files patched)
        squeue_responses = [
            _SQUEUE_RUNNING,
            _SQUEUE_RUNNING,
            _SQUEUE_RUNNING,
            _SQUEUE_EMPTY,
        ]
        tail_responses = [_TAIL_OUTPUT] * 3
        sacct_response = [_SACCT_COMPLETED]
        all_responses = (
            [squeue_responses[0]]
            + [tail_responses[0]]
            + [squeue_responses[1]]
            + [tail_responses[1]]
            + [squeue_responses[2]]
            + [tail_responses[2]]
            + [squeue_responses[3]]
            + sacct_response
        )

        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=all_responses,
            ) as mock_rit,
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
            patch("app.utils.scheduler.sleep"),
            patch("app.utils.scheduler.time", side_effect=[0.0] * 20),
        ):
            result = follow_submitted_job(_JOB_ID, timeout=3600)

        assert result is None
        # 4 squeue checks + 3 tail calls + 1 sacct = 8 total (no cat)
        assert mock_rit.call_count == 8

    def test_sleep_called_with_poll_interval(self):
        """sleep(2.0) must be called once per completed loop iteration."""
        # _tail_stdout is not triggered (os.path.exists not patched → file absent),
        # so run_in_terminal receives only: squeue_running, squeue_empty, sacct.
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_RUNNING,
                    _SQUEUE_EMPTY,
                    _SACCT_COMPLETED,
                ],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
            patch("app.utils.scheduler.sleep") as mock_sleep,
            patch("app.utils.scheduler.time", side_effect=[0.0, 1.0, 2.0]),
        ):
            follow_submitted_job(_JOB_ID, timeout=3600)

        mock_sleep.assert_called_once_with(2.0)


class TestMissingStdout:
    def test_warning_logged_no_exception_raised(self):
        """If stdout.modelops does not exist after completion, warn but not raise."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_EMPTY, _SACCT_COMPLETED],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_MISSING,
            ),
        ):
            # Must not raise
            result = follow_submitted_job(_JOB_ID, timeout=60)

        assert result is None

    def test_warning_printed_when_no_logger(self, capsys):
        """Without a logger, the warning is sent to stdout via print()."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_EMPTY, _SACCT_COMPLETED],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_MISSING,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "stdout.modelops" in captured.out

    def test_warning_sent_to_logger_when_provided(self):
        """With a logger, the warning goes through logger.info(), not print()."""
        mock_logger = MagicMock()

        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_EMPTY, _SACCT_COMPLETED],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_MISSING,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60, logger=mock_logger)

        assert mock_logger.info.called
        warning_calls = [
            str(c) for c in mock_logger.info.call_args_list if "Warning" in str(c)
        ]
        assert warning_calls


class TestTimeout:
    def test_runtime_error_raised_on_timeout(self):
        """RuntimeError must be raised when elapsed time exceeds timeout."""
        # squeue always returns running; time() returns values that exceed timeout
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_RUNNING,
                    _TAIL_OUTPUT,
                ],
            ),
            patch("app.utils.scheduler.os.path.exists", return_value=True),
            patch("app.utils.scheduler.sleep"),
            # start_time=0, then elapsed=999 (> timeout=10)
            patch(
                "app.utils.scheduler.time",
                side_effect=[0.0, 999.0],
            ),
        ):
            with pytest.raises(RuntimeError, match="Timeout"):
                follow_submitted_job(_JOB_ID, timeout=10)

    def test_timeout_error_mentions_job_id(self):
        """The RuntimeError message must include the job_id for traceability."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_RUNNING, _TAIL_OUTPUT],
            ),
            patch("app.utils.scheduler.os.path.exists", return_value=True),
            patch("app.utils.scheduler.sleep"),
            patch(
                "app.utils.scheduler.time",
                side_effect=[0.0, 999.0],
            ),
        ):
            with pytest.raises(RuntimeError, match=_JOB_ID):
                follow_submitted_job(_JOB_ID, timeout=10)


class TestSqueueFailure:
    def test_runtime_error_raised_on_squeue_non_zero(self):
        """If squeue returns a non-zero exit code, RuntimeError must be raised."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                return_value=_SQUEUE_ERROR,
            ),
            patch("app.utils.scheduler.os.path.exists", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="squeue failed"):
                follow_submitted_job(_JOB_ID, timeout=60)

    def test_runtime_error_raised_on_squeue_none_code(self):
        """If squeue returns None exit code, RuntimeError must be raised."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                return_value=(None, []),
            ),
            patch("app.utils.scheduler.os.path.exists", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="squeue failed"):
                follow_submitted_job(_JOB_ID, timeout=60)


class TestBackwardCompatibility:
    def test_called_without_logger_behaves_same_as_logger_none(self):
        """Calling follow_submitted_job(job_id, timeout) without logger must work."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_EMPTY, _SACCT_COMPLETED],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
        ):
            # Must not raise TypeError about missing argument
            result = follow_submitted_job(_JOB_ID, 60)

        assert result is None


class TestGetJobCompletionInfo:
    def test_returns_job_completion_info_on_valid_output(self):
        """Valid sacct output parses to a fully-populated JobCompletionInfo."""
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [f"{_JOB_ID}|COMPLETED|0:0|00:05:23|4096K"]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is not None
        assert result.job_id == _JOB_ID
        assert result.state == "COMPLETED"
        assert result.exit_code == "0:0"
        assert result.elapsed == "00:05:23"
        assert result.max_rss == "4096K"
        assert result.raw_output == f"{_JOB_ID}|COMPLETED|0:0|00:05:23|4096K"

    def test_returns_none_on_non_zero_exit_code(self):
        """sacct returning a non-zero exit code yields None."""
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SACCT_ERROR,
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_none_on_none_exit_code(self):
        """sacct returning None exit code (timeout) yields None."""
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SACCT_NONE,
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_unknown_state_on_unparseable_output(self):
        """When sacct output cannot be parsed (too few fields), state is UNKNOWN."""
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [f"{_JOB_ID}|COMPLETED"]),  # only 2 fields instead of 5
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is not None
        assert result.state == "UNKNOWN"
        assert result.raw_output == f"{_JOB_ID}|COMPLETED"

    def test_filters_batch_suffix_lines(self):
        """sacct output with base job + .batch line uses only the base line."""
        batch_line = f"{_JOB_ID}.batch|COMPLETED|0:0|00:05:23|2048K"
        base_line = f"{_JOB_ID}|COMPLETED|0:0|00:05:23|4096K"
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [base_line, batch_line]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is not None
        assert result.max_rss == "4096K"
        assert result.raw_output == base_line

    def test_batch_only_output_returns_none_when_no_base_line(self):
        """If only .batch lines exist (no base job line), returns None."""
        batch_line = f"{_JOB_ID}.batch|COMPLETED|0:0|00:05:23|2048K"
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [batch_line]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_none_on_exception(self):
        """Any unexpected exception from run_in_terminal yields None."""
        with patch(
            "app.utils.scheduler.run_in_terminal",
            side_effect=RuntimeError("connection refused"),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_none_on_empty_output(self):
        """Empty sacct output (no matching job line) yields None."""
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [""]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None


class TestFollowSubmittedJobSacctLogging:
    def test_completion_info_logged_after_loop_exit(self, capsys):
        """After loop exits, sacct state is logged to stdout."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_EMPTY,
                    _SACCT_COMPLETED,
                ],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        captured = capsys.readouterr()
        assert "COMPLETED" in captured.out
        assert _JOB_ID in captured.out

    def test_warning_logged_for_failed_state(self, capsys):
        """When sacct reports FAILED, a WARNING line is logged."""
        sacct_failed = (0, [f"{_JOB_ID}|FAILED|1:0|00:01:05|1024K"])
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_EMPTY,
                    sacct_failed,
                ],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "FAILED" in captured.out

    def test_no_warning_logged_for_completed_state(self, capsys):
        """When sacct reports COMPLETED, no WARNING line appears in sacct output."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_EMPTY,
                    _SACCT_COMPLETED,
                ],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        captured = capsys.readouterr()
        # sacct-driven WARNING must not appear; stdout logging has no WARNING prefix
        assert "WARNING: Job" not in captured.out

    def test_sacct_failure_does_not_propagate(self):
        """If sacct fails, follow_submitted_job still returns None without raising."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_EMPTY,
                    _SACCT_ERROR,
                ],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
        ):
            result = follow_submitted_job(_JOB_ID, timeout=60)

        assert result is None


class TestFollowSubmittedJobStderrLogging:
    def test_stderr_lines_prefixed_with_stderr_label(self, capsys):
        """When stderr.modelops is non-empty, log lines are prefixed with STDERR:."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_EMPTY, _SACCT_COMPLETED],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_BOTH,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        captured = capsys.readouterr()
        assert "STDERR:" in captured.out
        assert "Warning: deprecated config" in captured.out

    def test_no_stderr_label_when_stderr_absent(self, capsys):
        """When stderr.modelops is absent, no STDERR: lines appear."""
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[_SQUEUE_EMPTY, _SACCT_COMPLETED],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
        ):
            follow_submitted_job(_JOB_ID, timeout=60)

        captured = capsys.readouterr()
        assert "STDERR:" not in captured.out
