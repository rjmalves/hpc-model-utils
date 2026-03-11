import hashlib
from os import curdir, listdir, stat
from os.path import isfile, join

from app.utils.constants import FILE_SIZE_LIMIT_FOR_HASHING_BYTES
from app.utils.fs import list_files_by_regexes


def hash_string(data: str) -> str:
    """
    Evaluates a hash string from another string.
    """
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def hash_object(obj: object) -> str:
    """
    Evaluates a hash string from the object's __dict__ attribute. It
    is a simple but effective way for identifying uniquely an object
    that might be used for caching purposes.
    """
    return hashlib.md5(str(obj.__dict__).encode("utf-8")).hexdigest()


def hash_file(filepath: str) -> str:
    """
    Evaluates a hash string from the binary content of a file.
    """
    with open(filepath, "rb") as fp:
        return hashlib.md5(fp.read()).hexdigest()


def hash_all_files_in_path(
    path: str = curdir,
    file_size_limit_bytes: int = FILE_SIZE_LIMIT_FOR_HASHING_BYTES,
    file_regexes_to_ignore: list[str] = [],
) -> tuple[str, list[str]]:
    """
    Hashes all the files in a given directory and
    combine their hashes in a single string.
    """
    files_in_path = [join(path, f) for f in listdir(path) if isfile(f)]
    files_in_limit = [
        f for f in files_in_path if stat(f).st_size <= file_size_limit_bytes
    ]
    files_matching_ignore_patterns = list_files_by_regexes(
        [], file_regexes_to_ignore, path=path
    )
    files_matching_ignore_patterns = [
        join(path, f) for f in files_matching_ignore_patterns
    ]
    files_to_hash = [
        f for f in files_in_limit if f not in files_matching_ignore_patterns
    ]
    file_hashes = [hash_file(f) for f in files_to_hash]
    return hash_string("".join(file_hashes)), files_to_hash
