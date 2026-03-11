import re
from concurrent.futures import ThreadPoolExecutor, wait
from os import chmod, curdir, listdir, remove
from os.path import isdir, isfile, join
from pathlib import Path
from shutil import move, rmtree
from zipfile import ZIP_DEFLATED, ZipFile

from app.utils.zipfileparallel import ZipFileParallel


def change_file_permission(filepath: str, permission_code: int):
    chmod(filepath, permission_code)


def extract_zip_content(
    filepath: str, members: list[str] | None = None, patterns: list[str] = []
) -> list[str]:
    with ZipFile(filepath) as file:
        if members:
            members = [m for m in members if m in file.namelist()]
        elif len(patterns) > 0:
            members = [
                m
                for m in file.namelist()
                if any(re.search(p, m) for p in patterns)
            ]
        else:
            members = None
        file.extractall(members=members)
        return file.namelist()


def moves_content_to_rootdir(subdir: str):
    if isdir(subdir):
        for a in listdir(subdir):
            if isfile(join(subdir, a)):
                move(join(subdir, a), a)
        rmtree(subdir)


def list_files_by_regexes(
    files_to_ignore: list[str], regexes: list[str], path: str = curdir
):
    regex = r"|".join(regexes)
    regex = r"(" + regex + r")"
    files: list[str] = []
    for a in listdir(path):
        if a not in files_to_ignore:
            if re.search(regex, a) is not None:
                files.append(a)

    return files


def compress_files_to_zip(filenames: list[str], compressed_filename: str):
    with ZipFile(
        join(curdir, f"{compressed_filename}.zip"),
        "w",
        compression=ZIP_DEFLATED,
    ) as zip_file:
        for a in sorted(filenames):
            if isfile(join(curdir, a)):
                zip_file.write(a)


def _add_file_to_parallel_zip(handle: ZipFileParallel, filepath: Path):
    data = filepath.read_bytes()
    handle.writestr(str(filepath.name), data)


def compress_files_to_zip_parallel(
    filenames: list[str], compressed_filename: str, num_cpus: int
):
    filepaths = [Path(a) for a in filenames if isfile(a)]
    # TODO - consider each file size while distributing
    # files in threads
    with ZipFileParallel(
        join(curdir, f"{compressed_filename}.zip"),
        "w",
        compression=ZIP_DEFLATED,
    ) as handle:
        with ThreadPoolExecutor(num_cpus) as exe:
            fs = [
                exe.submit(_add_file_to_parallel_zip, handle, f)
                for f in filepaths
            ]

        wait(fs)
        for future in fs:
            future.result()


def clean_files(files: list[str]):
    for a in files:
        if isfile(join(curdir, a)):
            remove(a)


def find_file_case_insensitive(path: str, candidate_filename: str) -> str:
    """
    Finds a file in a directory, case insensitive. Returns the full path.
    """
    upper_filename = candidate_filename.upper()
    lower_filename = candidate_filename.lower()
    for try_filename in [candidate_filename, upper_filename, lower_filename]:
        fullpath = Path(path).joinpath(try_filename)
        if fullpath.exists():
            return str(fullpath)
    raise FileNotFoundError(f"File {candidate_filename} not found in {path}")
