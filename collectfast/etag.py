import hashlib
import logging

from django.core.cache import caches
from minio.error import ResponseError
from minio_storage.storage import MinioStorage
from storages.utils import safe_join

from collectfast import settings

try:
    from functools import lru_cache
except ImportError:
    # make lru_cache do nothing in python 2.7
    def lru_cache(maxsize=128, typed=False):
        def decorator(func):
            return func
        return decorator

cache = caches[settings.cache]
logger = logging.getLogger(__name__)


@lru_cache()
def get_cache_key(path):
    """
    Create a cache key by concatenating the prefix with a hash of the path.
    """
    # Python 2/3 support for path hashing
    try:
        path_hash = hashlib.md5(path).hexdigest()
    except TypeError:
        path_hash = hashlib.md5(path.encode('utf-8')).hexdigest()
    return settings.cache_key_prefix + path_hash


def get_remote_etag(storage, prefixed_path):
    """
    Get etag of path from S3 using boto or boto3.
    """
    if isinstance(storage, MinioStorage):
        try:
            normalized_path = prefixed_path.replace('\\', '/')
            return storage.client.stat_object(storage.bucket_name, normalized_path)
        except ResponseError:
            pass
    normalized_path = safe_join(storage.location, prefixed_path).replace(
        '\\', '/')
    try:
        return storage.bucket.get_key(normalized_path).etag
    except AttributeError:
        pass
    try:
        return storage.bucket.Object(normalized_path).e_tag
    except:
        pass
    return None


def get_etag(storage, path, prefixed_path):
    """
    Get etag of path from cache or S3 - in that order.
    """
    cache_key = get_cache_key(path)
    etag = cache.get(cache_key, False)
    if etag is False:
        etag = get_remote_etag(storage, prefixed_path)
        cache.set(cache_key, etag)
    return etag


def destroy_etag(path):
    """
    Clear etag of path from cache.
    """
    cache.delete(get_cache_key(path))


def get_file_hash(storage, path):
    """
    Create md5 hash from file contents.
    """
    contents = storage.open(path).read()
    file_hash = '"%s"' % hashlib.md5(contents).hexdigest()
    return file_hash


def has_matching_etag(remote_storage, source_storage, path, prefixed_path):
    """
    Compare etag of path in source storage with remote.
    """
    storage_etag = get_etag(remote_storage, path, prefixed_path)
    local_etag = get_file_hash(source_storage, path)
    return storage_etag == local_etag


def should_copy_file(remote_storage, path, prefixed_path, source_storage):
    """
    Returns True if the file should be copied, otherwise False.
    """
    if has_matching_etag(
            remote_storage, source_storage, path, prefixed_path):
        logger.info("%s: Skipping based on matching file hashes" % path)
        return False

    # Invalidate cached versions of lookup before copy
    destroy_etag(path)
    logger.info("%s: Hashes did not match" % path)
    return True
