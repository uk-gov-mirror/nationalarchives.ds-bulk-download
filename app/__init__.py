import logging
import os

from flask import Flask
from jinja2 import ChoiceLoader, PackageLoader
from tna_utilities.datetime import pretty_date, pretty_date_range, pretty_datetime
from tna_utilities.number import pretty_file_size

from app.lib.context_processor import cookie_preference, now_iso_8601
from app.lib.talisman import talisman
from app.lib.template_filters import slugify


def create_app(config_class):
    app_url_path = "/bulk-download"

    app = Flask(__name__, static_url_path=f"{app_url_path}/static")
    app.config.from_object(config_class)

    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    app.logger.handlers.extend(gunicorn_error_logger.handlers)
    app.logger.setLevel(
        gunicorn_error_logger.level or os.getenv("LOG_LEVEL", "warning").upper()
    )

    talisman.init_app(
        app,
        content_security_policy=app.config["CONTENT_SECURITY_POLICY"],
        allow_google_content_security_policy=True,
        allow_typekit_content_security_policy=True,
        force_https=app.config["FORCE_HTTPS"],
    )

    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    app.jinja_env.loader = ChoiceLoader(
        [
            PackageLoader("app"),
            PackageLoader("tna_frontend_jinja"),
        ]
    )
    app.jinja_env.add_extension("jinja2.ext.do")

    @app.context_processor
    def context_processor():
        return dict(
            cookie_preference=cookie_preference,
            now_iso_8601=now_iso_8601,
            app_config={
                "ENVIRONMENT_NAME": app.config["ENVIRONMENT_NAME"],
                "CONTAINER_IMAGE": app.config["CONTAINER_IMAGE"],
                "BUILD_VERSION": app.config["BUILD_VERSION"],
                "TNA_FRONTEND_VERSION": app.config["TNA_FRONTEND_VERSION"],
                "COOKIE_DOMAIN": app.config["COOKIE_DOMAIN"],
                "COOKIE_PREFERENCES_URL": app.config["COOKIE_PREFERENCES_URL"],
                "GA4_ID": app.config["GA4_ID"],
                "S3_EXPORT_BUCKET_HOST_URL": app.config["S3_EXPORT_BUCKET_HOST_URL"],
                "MERLIN_FILENAME_REPORT_URL": app.config["MERLIN_FILENAME_REPORT_URL"],
            },
            feature={},
            pretty_date_range=pretty_date_range,
        )

    app.add_template_filter(pretty_date)
    app.add_template_filter(pretty_datetime)
    app.add_template_filter(pretty_file_size)
    app.add_template_filter(slugify)

    from .healthcheck import bp as healthcheck_bp
    from .main import bp as site_bp
    from .sitemap import bp as sitemap_bp

    app.register_blueprint(healthcheck_bp, url_prefix="/healthcheck")
    app.register_blueprint(sitemap_bp, url_prefix=app_url_path)
    app.register_blueprint(site_bp)

    return app
