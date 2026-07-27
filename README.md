# TNA Python Flask Application

## Quickstart

```sh
# Build and start the container
docker compose up -d
```

1. Open http://localhost:65490/
   - Username: `admin`
   - Password: `password123`
1. Add some files into the `merlin` bucket (http://localhost:65490/browser/merlin)
1. In the `app` container, run `poetry run python tasks/process.py merlin all`
1. Check http://localhost:65491/merlin/ for the zipped file

### Add the static assets

During the first time install, your `app/static/assets` directory will be empty.

As you mount the project directory to the `/app` volume, the static assets from TNA Frontend installed inside the container will be "overwritten" by your empty directory.

To add back in the static assets, run:

```sh
docker compose exec app cp -r /app/node_modules/@nationalarchives/frontend/nationalarchives/assets /app/app/static
```

### Run tests

```sh
docker compose exec app poetry run python -m pytest
```

### Format and lint code

```sh
docker compose exec app format
```

## Environment variables

In addition to the [base Docker image variables](https://github.com/nationalarchives/docker/blob/main/docker/tna-python/README.md#environment-variables), this application has support for:

| Variable                     | Purpose                                                              | Default                                    |
| ---------------------------- | -------------------------------------------------------------------- | ------------------------------------------ |
| `CONFIG`                     | The configuration to use                                             | `config.Production`                        |
| `DEBUG`                      | If true, allow debugging[^1]                                         | `False`                                    |
| `COOKIE_DOMAIN`              | The domain to save cookie preferences against                        | _none_                                     |
| `COOKIE_PREFERENCES_URL`     | The URL for changing cookie preferences                              | _none_                                     |
| `CSP_IMG_SRC`                | A comma separated list of CSP rules for `img-src`                    | `'self'`                                   |
| `CSP_SCRIPT_SRC`             | A comma separated list of CSP rules for `script-src`                 | `'self'`                                   |
| `CSP_STYLE_SRC`              | A comma separated list of CSP rules for `style-src`                  | `'self'`                                   |
| `CSP_FONT_SRC`               | A comma separated list of CSP rules for `font-src`                   | `'self'`                                   |
| `CSP_CONNECT_SRC`            | A comma separated list of CSP rules for `connect-src`                | `'self'`                                   |
| `CSP_MEDIA_SRC`              | A comma separated list of CSP rules for `media-src`                  | `'self'`                                   |
| `CSP_WORKER_SRC`             | A comma separated list of CSP rules for `worker-src`                 | `'self'`                                   |
| `CSP_FRAME_SRC`              | A comma separated list of CSP rules for `frame-src`                  | `'self'`                                   |
| `CSP_FRAME_ANCESTORS`        | A comma separated list of CSP rules for `frame-accestors`            | `'self'`                                   |
| `CSP_REPORT_URI`             | The URL to report CSP violations to                                  | _none_                                     |
| `FORCE_HTTPS`                | Redirect requests to HTTPS as part of the CSP                        | _none_                                     |
| `GA4_ID`                     | The Google Analytics 4 ID                                            | _none_                                     |
| `AWS_DEFAULT_REGION`         | The default AWS region                                               | `eu-west-2`                                |
| `S3_EXPORT_BUCKET`           | The S3 bucket where the ZIP files and manifest should be uploaded to | _none_                                     |
| `S3_EXPORT_PREFIX_MERLIN`    | The S3 bucket folder for the Merlin files and manifest               | `merlin`                                   |
| `S3_MANIFEST_NAME`           | The S3 location of the Merlin files manifest                         | `manifest.json`                            |
| `S3_SOURCE_BUCKET_MERLIN`    | The S3 bucket containing all the of Merlin files                     | _none_                                     |
| `S3_SOURCE_PREFIX_MERLIN`    | The S3 prefix for the source Merlin files                            | _none_                                     |
| `S3_EXPORT_BUCKET_HOST_URL`  | The domain which serves the ZIP files                                | `https://download.nationalarchives.gov.uk` |
| `MERLIN_FILENAME_REPORT_URL` | The URL for the Merlin filename report                               | _none_                                     |

[^1] [Debugging in Flask](https://flask.palletsprojects.com/en/2.3.x/debugging/)
