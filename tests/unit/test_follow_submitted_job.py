from unittest.mock import MagicMock, patch

import pytest

from app.utils.constants import SLURM_SUBMISSION_REGEX_PATTERN
from app.utils.scheduler import (
    JobCompletionInfo,
    JobOutputFiles,
    cancel_submitted_job,
    follow_submitted_job,
    get_job_completion_info,
    read_job_output_files,
    submit_job,
    wait_cancelled_job,
)

_JOB_ID = "12345"

_SQUEUE_RUNNING = (0, ["12345 normal mymodel user PD 0:00 1 (Resources)"])
_SQUEUE_EMPTY = (0, [""])
_SQUEUE_ERROR = (1, ["slurm_load_jobs error: Invalid job id specified"])
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
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stdout.modelops").write_text("Model converged successfully\n")
        (tmp_path / "stderr.modelops").write_text("Warning: deprecated config\n")

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stderr_exists is True
        assert result.stdout_content == "Model converged successfully\n"
        assert result.stderr_content == "Warning: deprecated config\n"

    def test_only_stdout_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stdout.modelops").write_text("output\n")

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stderr_exists is False
        assert result.stdout_content == "output\n"
        assert result.stderr_content is None

    def test_neither_file_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = read_job_output_files()

        assert result.stdout_exists is False
        assert result.stderr_exists is False
        assert result.stdout_content is None
        assert result.stderr_content is None

    def test_large_stdout_truncated_to_10000_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        total_lines = 50_000
        lines = [f"line {i}\n" for i in range(total_lines)]
        (tmp_path / "stdout.modelops").write_text("".join(lines))

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stdout_content is not None
        returned_lines = result.stdout_content.splitlines()
        assert len(returned_lines) == 10_000
        assert returned_lines[-1] == f"line {total_lines - 1}"
        assert returned_lines[0] == f"line {total_lines - 10_000}"

    def test_non_utf8_bytes_handled_with_replace(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stdout.modelops").write_bytes(b"valid text\xff\xfe invalid bytes\n")

        result = read_job_output_files()

        assert result.stdout_exists is True
        assert result.stdout_content is not None
        assert "valid text" in result.stdout_content

    def test_custom_filenames_accepted(self, tmp_path, monkeypatch):
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

        assert result.stdout_exists is True
        assert result.stdout_content is None


class TestFastJob:
    def test_completion_info_logged_after_instant_exit(self, capsys):
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_EMPTY,
                    _SACCT_COMPLETED,
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
        mock_sleep.assert_not_called()
        assert mock_rit.call_count == 2
        captured = capsys.readouterr()
        assert "COMPLETED" in captured.out
        assert _JOB_ID in captured.out

    def test_no_sleep_before_first_squeue_check(self):
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

        assert call_order[0] == "run_in_terminal"
        assert "sleep" not in call_order


class TestNormalJob:
    def test_new_output_streamed_each_iteration(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        stdout_path = tmp_path / "stdout.modelops"
        stdout_path.write_text("line 1\nline 2\n")

        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                side_effect=[
                    _SQUEUE_RUNNING,
                    _SQUEUE_RUNNING,
                    _SQUEUE_RUNNING,
                    _SQUEUE_EMPTY,
                    _SACCT_COMPLETED,
                ],
            ),
            patch(
                "app.utils.scheduler.read_job_output_files",
                return_value=_OUTPUT_STDOUT_ONLY,
            ),
            patch("app.utils.scheduler.sleep"),
            patch("app.utils.scheduler.time", side_effect=[0.0] * 20),
        ):
            result = follow_submitted_job(_JOB_ID, timeout=3600)

        assert result is None
        captured = capsys.readouterr()
        # Lines should appear exactly once (streaming, not repeated)
        assert captured.out.count("line 1") == 1
        assert captured.out.count("line 2") == 1

    def test_sleep_called_with_poll_interval(self):
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
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                return_value=_SQUEUE_RUNNING,
            ),
            patch("app.utils.scheduler.sleep"),
            patch(
                "app.utils.scheduler.time",
                side_effect=[0.0, 999.0],
            ),
        ):
            with pytest.raises(RuntimeError, match="Timeout"):
                follow_submitted_job(_JOB_ID, timeout=10)

    def test_timeout_error_mentions_job_id(self):
        with (
            patch(
                "app.utils.scheduler.run_in_terminal",
                return_value=_SQUEUE_RUNNING,
            ),
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
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SQUEUE_ERROR,
        ):
            with pytest.raises(RuntimeError, match="squeue failed"):
                follow_submitted_job(_JOB_ID, timeout=60)

    def test_runtime_error_raised_on_squeue_none_code(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(None, []),
        ):
            with pytest.raises(RuntimeError, match="squeue failed"):
                follow_submitted_job(_JOB_ID, timeout=60)


class TestBackwardCompatibility:
    def test_called_without_logger_behaves_same_as_logger_none(self):
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
            result = follow_submitted_job(_JOB_ID, 60)

        assert result is None


class TestGetJobCompletionInfo:
    def test_returns_job_completion_info_on_valid_output(self):
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
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SACCT_ERROR,
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_none_on_none_exit_code(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SACCT_NONE,
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_unknown_state_on_unparseable_output(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [f"{_JOB_ID}|COMPLETED"]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is not None
        assert result.state == "UNKNOWN"
        assert result.raw_output == f"{_JOB_ID}|COMPLETED"

    def test_filters_batch_suffix_lines(self):
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
        batch_line = f"{_JOB_ID}.batch|COMPLETED|0:0|00:05:23|2048K"
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [batch_line]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_none_on_exception(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            side_effect=RuntimeError("connection refused"),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None

    def test_returns_none_on_empty_output(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(0, [""]),
        ):
            result = get_job_completion_info(_JOB_ID)

        assert result is None


class TestFollowSubmittedJobSacctLogging:
    def test_completion_info_logged_after_loop_exit(self, capsys):
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
        assert "WARNING: Job" not in captured.out

    def test_sacct_failure_does_not_propagate(self):
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


_SUBMIT_JOB_ID = "67890"
_SBATCH_SUCCESS = (0, [f"Submitted batch job {_SUBMIT_JOB_ID}"])
_SBATCH_NONZERO = (1, ["sbatch: error: Batch job submission failed"])
_SBATCH_NO_MATCH = (0, ["Some unexpected output that does not match pattern"])
_SCANCEL_SUCCESS = (0, [])
_SCANCEL_FAILURE = (1, ["scancel: error: Kill job error on job id 12345: Invalid job id"])
_SQUEUE_GREP_SUCCESS = (0, [])
_SQUEUE_GREP_FAILURE = (1, ["timeout exceeded"])


class TestSubmitJob:
    def test_successful_submission_returns_job_id(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_SUCCESS,
        ):
            result = submit_job("normal", 64, "run.sh")

        assert result == _SUBMIT_JOB_ID

    def test_nonzero_exit_returns_none(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_NONZERO,
        ):
            result = submit_job("normal", 64, "run.sh")

        assert result is None

    def test_output_no_regex_match_returns_none(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_NO_MATCH,
        ):
            result = submit_job("normal", 64, "run.sh")

        assert result is None

    def test_max_tasks_per_node_adds_flag_to_command(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_SUCCESS,
        ) as mock_rit:
            submit_job("normal", 64, "run.sh", max_tasks_per_node=8)

        call_args = mock_rit.call_args
        command_list: list[str] = call_args[0][0]
        assert any("--ntasks-per-node=8" in arg for arg in command_list)

    def test_max_tasks_per_node_none_does_not_add_flag(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_SUCCESS,
        ) as mock_rit:
            submit_job("normal", 64, "run.sh", max_tasks_per_node=None)

        call_args = mock_rit.call_args
        command_list: list[str] = call_args[0][0]
        assert not any("--ntasks-per-node" in arg for arg in command_list)

    def test_max_job_time_hours_48_formats_as_two_days(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_SUCCESS,
        ) as mock_rit:
            submit_job("normal", 64, "run.sh", max_job_time_hours=48)

        call_args = mock_rit.call_args
        command_list: list[str] = call_args[0][0]
        assert "--time=2-00:00:00" in command_list

    def test_max_job_time_hours_none_does_not_add_time_flag(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SBATCH_SUCCESS,
        ) as mock_rit:
            submit_job("normal", 64, "run.sh", max_job_time_hours=None)

        call_args = mock_rit.call_args
        command_list: list[str] = call_args[0][0]
        assert not any(arg.startswith("--time=") for arg in command_list)

    def test_pattern_constant_matches_sbatch_output(self):
        import re

        output_line = "Submitted batch job 67890"
        match = re.match(SLURM_SUBMISSION_REGEX_PATTERN, output_line)

        assert match is not None
        assert match.groups()[0] == "67890"


class TestCancelSubmittedJob:
    def test_successful_cancel_returns_true(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SCANCEL_SUCCESS,
        ):
            result = cancel_submitted_job(_JOB_ID)

        assert result is True

    def test_failed_cancel_returns_false(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SCANCEL_FAILURE,
        ):
            result = cancel_submitted_job(_JOB_ID)

        assert result is False


class TestWaitCancelledJob:
    def test_successful_wait_returns_none(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SQUEUE_GREP_SUCCESS,
        ):
            result = wait_cancelled_job(_JOB_ID, timeout=60.0)

        assert result is None

    def test_nonzero_exit_raises_runtime_error(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=_SQUEUE_GREP_FAILURE,
        ):
            with pytest.raises(RuntimeError):
                wait_cancelled_job(_JOB_ID, timeout=60.0)

    def test_runtime_error_message_contains_status_code(self):
        with patch(
            "app.utils.scheduler.run_in_terminal",
            return_value=(1, ["timeout exceeded"]),
        ):
            with pytest.raises(RuntimeError, match="1"):
                wait_cancelled_job(_JOB_ID, timeout=60.0)
