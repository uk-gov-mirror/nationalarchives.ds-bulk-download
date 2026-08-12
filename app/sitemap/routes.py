from flask import current_app, make_response, render_template, url_for
from tna_utilities.flask import cacheable_duration

from app.sitemap import bp


@bp.route("/sitemap.xml")
@cacheable_duration(86400)
def sitemap():
    pages = list()
    for rule in current_app.url_map.iter_rules():
        if (
            str(rule) != "/"
            and not str(rule).startswith("/healthcheck")
            and not str(rule).startswith("/bulk-download")
            and "GET" in rule.methods
            and len(rule.arguments) == 0
        ):
            pages.append(url_for(rule.endpoint, _external=True, _scheme="https"))
    xml_sitemap_index = render_template("sitemap.xml", pages=pages)
    response = make_response(xml_sitemap_index)
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    return response
