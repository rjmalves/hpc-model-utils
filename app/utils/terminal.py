import subprocess
import time

from app.utils.constants import DEFAULT_SHELL_COMMAND_TIMEOUT


def run_in_terminal(
    cmds: list[str],
    timeout: float = DEFAULT_SHELL_COMMAND_TIMEOUT,
    log_output: bool = False,
    last_lines_diff: int = 1,
) -> tuple[int | None, list[str]]:
    """
    Runs a command in a shell subsession, waiting for
    some time limit before aborting.
    """
    cmd = " ".join(cmds)
    subprocess_pipe = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        universal_newlines=True,
    )
    starting_time = time.time()
    output_lines: list[str] = []
    while True:
        stdout = subprocess_pipe.stdout
        if stdout is None:
            raise ValueError(f"Error in subprocess execution: {cmd}")
        try:
            stdout_line = stdout.readline()
        except UnicodeDecodeError:
            stdout_line = ""
            if log_output:
                print(
                    "Erro de decodificação UTF-8 - linha contém caracteres inválidos!!!",
                    flush=True,
                )
        output_line = stdout_line.rstrip()
        if output_line not in output_lines[-last_lines_diff:]:
            if log_output:
                print(output_line, flush=True)
        output_lines.append(output_line)
        status_code = subprocess_pipe.poll()
        if status_code is not None:
            for line in stdout.readlines():
                output_line = line.rstrip()
                if output_line not in output_lines[-last_lines_diff:]:
                    if log_output:
                        print(output_line, flush=True)
                output_lines.append(output_line)
            break
        current_time = time.time()
        if current_time - starting_time > timeout:
            break
        time.sleep(0.1)
    subprocess_pipe.terminate()
    return status_code, output_lines


def cast_encoding_to_utf8(filepath: str):
    status_code, output = run_in_terminal(["file", "-i", filepath])
    if status_code != 0:
        raise ValueError(f"Error running command (file -i {filepath})")
    encoding_string = output[0].split("charset=")[1].strip()
    if "unknown" in encoding_string:
        encoding_string = "ISO-8859-1"
    if encoding_string not in ["utf-8", "us-ascii", "binary"]:
        encoding_string = encoding_string.upper()
        for script_line in [
            ["dos2unix", "-o", filepath],
            [
                "iconv",
                "-f",
                encoding_string,
                "-t",
                "UTF-8",
                filepath,
                "-o",
                filepath + ".tmp",
            ],
            ["mv", filepath + ".tmp", filepath],
        ]:
            status_code, output = run_in_terminal(script_line)
            if status_code != 0:
                raise ValueError(f"Error running command ({script_line})")
