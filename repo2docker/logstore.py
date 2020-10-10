import logging
import os
from tempfile import NamedTemporaryFile
import re
from traitlets import Any, Unicode, default
from traitlets.config import LoggingConfigurable

try:
    import boto3

    S3_ENABLED = True
except ImportError:
    S3_ENABLED = False
    pass


"""Match all ANSI escape codes https://superuser.com/a/380778"""
ansi_escape_regex = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class LogStore(LoggingConfigurable):
    """Abstract interface for a class that stores a build log.
    This default implementation does nothing."""

    logname = Unicode("", help="The name and/or path of the log", config=True)

    def write(self, s):
        """Write to the log"""
        pass

    def close(self):
        """Finish logging. Implementations may save or copy the log."""
        pass


class S3LogStore(LogStore):
    """Store a build log and upload to a S3 bucket on close"""

    # Connection details
    endpoint = Unicode(help="S3 endpoint", config=True)
    access_key = Unicode(help="S3 access key ", config=True)
    secret_key = Unicode(help="S3 secret key", config=True)
    session_token = Unicode("", help="S3 session token (optional)", config=True)
    region = Unicode("", help="S3 region (optional)", config=True)

    # Where to store the log
    bucket = Unicode(help="S3 bucket", config=True)
    keyprefix = Unicode("", help="Prefix log path with this", config=True)
    acl = Unicode(
        "public-read", help="ACL for the object, default public-read", config=True
    )

    _logfile = Any(allow_none=True)

    @default("_logfile")
    def _default_logfile(self):
        return NamedTemporaryFile("w", delete=False)

    def __init__(self, **kwargs):
        if not S3_ENABLED:
            raise RuntimeError("S3LogStore requires the boto3 library")
        super().__init__(**kwargs)
        self.log = logging.getLogger("repo2docker")

    def _s3_credentials(self):
        creds = dict(
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )
        if self.session_token:
            creds["aws_session_token"] = self.session_token
        return creds

    def write(self, s):
        """Write a log, newlines are not automatically added,
        removes ANSI terminal escape codes"""
        cleaned = ansi_escape_regex.sub("", str(s))
        self._logfile.write(cleaned)

    def close(self):
        """Upload the logfile to S3"""
        self._logfile.close()
        if not os.stat(self._logfile.name).st_size:
            # Empty log means image already exists so nothing was built
            return
        dest = f"{self.keyprefix}{self.logname}"
        self.log.info(
            f"Uploading log to {self.endpoint} bucket:{self.bucket} key:{dest}"
        )
        s3 = boto3.resource(
            "s3",
            config=boto3.session.Config(signature_version="s3v4"),
            **self._s3_credentials(),
        )
        s3.Bucket(self.bucket).upload_file(
            self._logfile.name,
            dest,
            ExtraArgs={"ContentType": "text/plain; charset=utf-8", "ACL": self.acl},
        )
        os.remove(self._logfile.name)
