import json
import os

from app.lib.util import strtobool


class Features:
    pass


class Production(Features):
    ENVIRONMENT_NAME: str = os.environ.get("ENVIRONMENT_NAME", "production")
    CONTAINER_IMAGE: str = os.environ.get("CONTAINER_IMAGE", "")
    BUILD_VERSION: str = os.environ.get("BUILD_VERSION", "")
    TNA_FRONTEND_VERSION: str = ""
    try:
        with open(
            os.path.join(
                os.path.realpath(os.path.dirname(__file__)),
                "node_modules/@nationalarchives/frontend",
                "package.json",
            )
        ) as package_json:
            try:
                data = json.load(package_json)
                TNA_FRONTEND_VERSION = data["version"] or ""
            except ValueError:
                pass
    except FileNotFoundError:
        pass

    SECRET_KEY: str = os.environ.get("SECRET_KEY", "")

    DEBUG: bool = False

    COOKIE_DOMAIN: str = os.environ.get("COOKIE_DOMAIN", ".nationalarchives.gov.uk")
    COOKIE_PREFERENCES_URL: str = os.environ.get("COOKIE_PREFERENCES_URL", "/cookies/")

    CSP_REPORT_URI: str = os.environ.get("CSP_REPORT_URI", "")
    if CSP_REPORT_URI and BUILD_VERSION:
        CSP_REPORT_URI += f"&sentry_release={BUILD_VERSION}" if BUILD_VERSION else ""
    CONTENT_SECURITY_POLICY: dict = {
        "connect-src": os.environ.get("CSP_CONNECT_SRC", "").split(","),
        "font-src": os.environ.get("CSP_FONT_SRC", "").split(","),
        "frame-ancestors": os.environ.get("CSP_FRAME_ANCESTORS", "").split(","),
        "frame-src": os.environ.get("CSP_FRAME_SRC", "").split(","),
        "img-src": os.environ.get("CSP_IMG_SRC", "").split(","),
        "media-src": os.environ.get("CSP_MEDIA_SRC", "").split(","),
        "report-uri": CSP_REPORT_URI,
        "script-src": os.environ.get("CSP_SCRIPT_SRC", "").split(","),
        "style-src": os.environ.get("CSP_STYLE_SRC", "").split(","),
        "worker-src": os.environ.get("CSP_WORKER_SRC", "").split(","),
    }
    FORCE_HTTPS: bool = strtobool(os.getenv("FORCE_HTTPS", "False"))
    PREFERRED_URL_SCHEME: str = os.getenv("PREFERRED_URL_SCHEME", "https")

    GA4_ID: str = os.environ.get("GA4_ID", "")

    AWS_DEFAULT_REGION: str = os.environ.get("AWS_DEFAULT_REGION", "eu-west-2")
    S3_ENDPOINT: str = os.environ.get("S3_ENDPOINT", None)
    S3_EXPORT_BUCKET: str = os.environ.get("S3_EXPORT_BUCKET", "")
    S3_EXPORT_PREFIX_MERLIN: str = os.environ.get("S3_EXPORT_PREFIX_MERLIN", "merlin")
    S3_MANIFEST_NAME: str = os.environ.get("S3_MANIFEST_NAME", "manifest.json")
    S3_EXPORT_BUCKET_HOST_URL: str = os.environ.get(
        "S3_EXPORT_BUCKET_HOST_URL", "https://download.nationalarchives.gov.uk"
    ).rstrip("/")
    MERLIN_FILENAME_REPORT_URL: str = os.environ.get("MERLIN_FILENAME_REPORT_URL", "")


class Staging(Production):
    DEBUG: bool = strtobool(os.getenv("DEBUG", "False"))


class Develop(Production):
    DEBUG: bool = strtobool(os.getenv("DEBUG", "False"))


class Test(Production):
    ENVIRONMENT_NAME: str = "test"
    BUILD_VERSION: str = "test"

    SECRET_KEY: str = "abc123"
    DEBUG: bool = True
    TESTING: bool = True
    EXPLAIN_TEMPLATE_LOADING: bool = True

    FORCE_HTTPS: bool = False
    PREFERRED_URL_SCHEME: str = "http"
