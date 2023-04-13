from servicelayer import settings
from servicelayer.archive.file import FileArchive

ARCHIVE_FILE = "file"
ARCHIVE_S3 = "s3"
ARCHIVE_GS = "gs"


def init_archive(
    archive_type=settings.ARCHIVE_TYPE,
    path=settings.ARCHIVE_PATH,
    bucket=settings.ARCHIVE_BUCKET,
    publication_bucket=settings.PUBLICATION_BUCKET,
    bucket_path=settings.ARCHIVE_BUCKET_PATH,
    path_prefixed=settings.ARCHIVE_PATH_PREFIXED,
):
    """Instantiate an archive object."""
    if archive_type == ARCHIVE_S3:
        from servicelayer.archive.s3 import S3Archive

        return S3Archive(
            bucket=bucket,
            publication_bucket=publication_bucket,
            path=bucket_path,
            path_prefixed=path_prefixed,
        )

    if archive_type == ARCHIVE_GS:
        from servicelayer.archive.gs import GoogleStorageArchive

        return GoogleStorageArchive(
            bucket=bucket,
            publication_bucket=publication_bucket,
            path=bucket_path,
            path_prefixed=path_prefixed,
        )

    return FileArchive(path=path)
