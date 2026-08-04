import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from stat import S_IFREG
from typing import Optional

import boto3
from boto3.s3.transfer import TransferConfig
from pydantic import BaseModel, TypeAdapter
from stream_zip import ZIP_64, stream_zip
from to_file_like_obj import to_file_like_obj

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


class BatchManifestItem(BaseModel):
    name: str
    file: str
    total_size: int
    file_count: int
    created_timestamp: datetime
    from_datetime: Optional[datetime] = None
    to_datetime: Optional[datetime] = None


class BatchManifest(BaseModel):
    packager: str
    packager_group: str
    updated: datetime = datetime.now()
    items: list[BatchManifestItem]


class FileBatch(BaseModel):
    manifest_data: BatchManifestItem
    files: list[dict]


class Packager:
    packager_name: Optional[str] = None
    packager_group: Optional[str] = None
    manifest_name: Optional[str] = None
    export_filename: str
    source: Optional[str] = None
    s3_client: Optional[boto3.client] = None
    scanned: bool

    def __init__(
        self,
        export_filename: Optional[str] = "all.zip",
        from_datetime: Optional[datetime] = None,
        to_datetime: Optional[datetime] = None,
    ):
        if not self.packager_name:
            raise ValueError(
                "You cannot instantiate the base Packager class directly. Please use a subclass."
            )
        self.export_filename = export_filename
        self.files = []
        self.scanned = False
        self.from_datetime = from_datetime
        self.to_datetime = to_datetime
        self.s3_export_bucket = os.environ.get("S3_EXPORT_BUCKET")
        if not self.s3_export_bucket:
            raise ValueError("S3_EXPORT_BUCKET environment variable is not set.")
        logger.info(
            f"Packager initialized with from_datetime: {self.from_datetime}, to_datetime: {self.to_datetime}"
        )

    def _get_s3_client(self) -> boto3.client:
        if not self.s3_client:
            s3_endpoint = os.environ.get("S3_ENDPOINT", None)
            self.s3_client = boto3.client(
                "s3",
                region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
                endpoint_url=s3_endpoint,
            )
        return self.s3_client

    def _get_all_s3_objects(self, **base_kwargs) -> list[dict]:
        s3_client = self._get_s3_client()
        continuation_token = None
        while True:
            list_kwargs = dict(MaxKeys=1000, **base_kwargs)
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token
            response = s3_client.list_objects_v2(**list_kwargs)
            yield from response.get("Contents", [])
            if not response.get("IsTruncated"):  # At the end of the list?
                break
            continuation_token = response.get("NextContinuationToken")

    def scan(self, source: tuple[str, Optional[str]] = None) -> None:
        if source is None:
            raise ValueError("Source must be provided for scanning.")
        self.source = source
        logger.info(f"Scanning source: {self.source}")
        self.files = list(
            self._get_all_s3_objects(Bucket=self.source[0], Prefix=self.source[1])
        )
        logger.info(f"-- Found {len(self.files)} total files in source")
        if self.from_datetime and self.to_datetime:
            self.files = [
                file
                for file in self.files
                if self.from_datetime <= file["LastModified"] <= self.to_datetime
            ]
        elif self.from_datetime:
            self.files = [
                file
                for file in self.files
                if file["LastModified"] >= self.from_datetime
            ]
        elif self.to_datetime:
            self.files = [
                file for file in self.files if file["LastModified"] <= self.to_datetime
            ]
        self.scanned = True
        logger.info(f"-- Found {len(self.files)} files after filtering by date range")

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunk = FileBatch(
            manifest_data=BatchManifestItem(
                name="All files",
                file=f"{self.export_prefix}/{self.export_filename}"
                if self.export_prefix
                else self.export_filename,
                total_size=sum(file["Size"] for file in self.files),
                file_count=len(self.files),
                created_timestamp=datetime.now(timezone.utc),
                from_datetime=self.from_datetime,
                to_datetime=self.to_datetime,
            ),
            files=self.files,
        )
        logger.debug(f"-- Created chunk with {len(chunk.files)} files")
        return [chunk]

    def _get_existing_manifest(self, manifest_name: str) -> BatchManifest:
        logger.debug(f"Fetching existing manifest for: {manifest_name}")
        s3_client = self._get_s3_client()
        try:
            content_object = s3_client.get_object(
                Bucket=self.s3_export_bucket, Key=manifest_name
            )
            file_content = content_object.get("Body").read().decode("utf-8")
        except s3_client.exceptions.NoSuchKey:
            logger.debug(f"-- No existing manifest found for: {manifest_name}")
            return BatchManifest(
                packager=self.packager_name,
                packager_group=self.packager_group,
                items=[],
            )
        logger.debug(f"-- Existing manifest content: {file_content}")
        json_content = json.loads(file_content)
        try:
            items = TypeAdapter(list[BatchManifestItem]).validate_python(
                json_content["items"]
            )
        except Exception as e:
            logger.error(f"Error parsing manifest items: {e}")
            items = []
        return BatchManifest(
            packager=json_content.get("packager", self.packager_name),
            packager_group=json_content.get("packager_group", self.packager_group),
            items=items,
        )

    def process(
        self, manifest_name: str | None = None, export_prefix: str | None = None
    ) -> None:
        if not self.source:
            raise ValueError("Source must be provided for processing.")
        if manifest_name is None:
            raise ValueError("Manifest name must be provided for processing.")
        if export_prefix is None:
            raise ValueError("Export prefix must be provided for processing.")
        self.manifest_name = manifest_name
        self.export_prefix = export_prefix

        if not self.scanned:
            raise ValueError("No files to process. Try running scan() first.")
        if not self.files:
            logger.info("No files to process after scanning. Exiting.")
            return

        existing_manifest = self._get_existing_manifest(
            f"{self.export_prefix}/{self.manifest_name}"
        )

        chunked_files = self._chunk()
        logger.info(f"Processing {len(chunked_files)} chunks of files")
        for chunk in chunked_files:
            logger.debug(f"-- Chunk: {chunk.manifest_data}")
            for file in chunk.files:
                logger.debug(
                    f"---- File: {file['Key']} ({file['LastModified']}) - Size: {file['Size']} bytes"
                )
            self._zip_and_upload(chunk)

        self._post_process(existing_manifest, chunked_files)

    def _zip_and_upload(self, chunk: FileBatch) -> None:
        logger.info(
            f"Zipping and uploading chunk: {chunk.manifest_data.name} ({chunk.manifest_data.file})"
        )
        s3_client = self._get_s3_client()

        def member_files():
            # modified_at = datetime.now()
            mode = S_IFREG | 0o600
            for file in chunk.files:
                logger.debug(
                    f"-- Adding S3 file to ZIP: {self.source[0]}/{file['Key']}"
                )
                infile_object = s3_client.get_object(
                    Bucket=self.source[0], Key=file["Key"]
                )
                infile_content = infile_object["Body"].read()
                yield (
                    file["Key"],
                    file["LastModified"],
                    mode,
                    ZIP_64,
                    (infile_content,),
                )

        zipped_chunks = stream_zip(member_files())
        zipped_chunks_obj = to_file_like_obj(zipped_chunks)
        # ------------------------------------------
        # Since we're streaming the final total size
        # is unknown we have to tell boto3 what part
        # size to use to accommodate the entire file
        # and S3 has a hard limit of 10000 parts; in
        # this example we have a part size of 200MB,
        # so 2TB maximum final object size
        # ------------------------------------------
        s3_client.upload_fileobj(
            Fileobj=zipped_chunks_obj,
            Bucket=self.s3_export_bucket,
            Key=chunk.manifest_data.file,
            Config=TransferConfig(multipart_chunksize=1024 * 1024 * 200),
        )

    def _generate_manifest_items(self, chunked_files: list[FileBatch]) -> list[dict]:
        return [chunk.manifest_data.model_dump(mode="json") for chunk in chunked_files]

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return existing_manifest_items

    def _post_process(
        self, existing_manifest: BatchManifest, chunked_files: list[FileBatch]
    ) -> None:
        logger.info("Post-processing tasks")
        if existing_manifest.packager_group == self.packager_group:
            items_to_remove = self._manifest_items_to_remove(existing_manifest.items)
            item_names_to_remove = [item.name for item in items_to_remove]
            logger.debug(f"-- Items to remove from manifest: {item_names_to_remove}")
            items_to_keep = [
                item.model_dump(mode="json")
                for item in existing_manifest.items
                if item.name not in item_names_to_remove
            ]
        else:
            logger.debug(
                f"-- Removing all items from existing manifest due to packager group mismatch ({existing_manifest.packager_group} != {self.packager_group})"
            )
            items_to_keep = []
        new_items = self._generate_manifest_items(chunked_files)
        new_manifest = BatchManifest(
            packager=self.packager_name,
            packager_group=self.packager_group,
            items=items_to_keep + new_items,
        ).model_dump(mode="json")
        self._save_manifest(new_manifest)

    def _save_manifest(self, manifest_content: dict) -> None:
        logger.info(f"Save manifest: {self.export_prefix}/{self.manifest_name}")
        manifest_json = json.dumps(manifest_content, indent=4)
        logger.debug(manifest_json)
        s3_client = self._get_s3_client()
        s3_client.put_object(
            Bucket=self.s3_export_bucket,
            Key=f"{self.export_prefix}/{self.manifest_name}",
            Body=manifest_json,
            ContentType="application/json",
        )


class ThisWeekPackager(Packager):
    packager_name = "this_week"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        from_datetime = (
            today_datetime - timedelta(days=today_datetime.weekday())
        ).replace(hour=0, minute=0, second=0, microsecond=0)
        to_datetime = (
            today_datetime + timedelta(days=6 - today_datetime.weekday())
        ).replace(hour=23, minute=59, second=59, microsecond=999999)
        mondays_this_month = [
            (from_datetime.replace(day=1) + timedelta(days=i)).date()
            for i in range(from_datetime.day)
            if (from_datetime.replace(day=1) + timedelta(days=i)).weekday() == 0
        ]
        self.week_index = len(mondays_this_month)
        if from_datetime.replace(day=1).weekday() != 0:
            self.week_index += 1
        export_filename = f"{from_datetime.strftime('%Y-%m')}-w{self.week_index}.zip"
        self.name = f"{from_datetime.strftime('%B %Y')} (week {self.week_index})"
        super().__init__(
            export_filename=export_filename,
            from_datetime=from_datetime,
            to_datetime=to_datetime,
        )

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunk = FileBatch(
            manifest_data=BatchManifestItem(
                name=self.name,
                file=f"{self.export_prefix}/{self.export_filename}"
                if self.export_prefix
                else self.export_filename,
                total_size=sum(file["Size"] for file in self.files),
                file_count=len(self.files),
                created_timestamp=datetime.now(timezone.utc),
                from_datetime=self.from_datetime,
                to_datetime=self.to_datetime,
            ),
            files=self.files,
        )
        return [chunk]

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime == self.from_datetime
            and item.to_datetime == self.to_datetime
        ]


class AllWeeksThisMonthPackager(Packager):
    packager_name = "all_weeks_this_month"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        from_datetime = today_datetime.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        to_datetime = datetime(
            from_datetime.year,
            from_datetime.month,
            (
                (from_datetime + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            ).day,
            23,
            59,
            59,
            999999,
            tzinfo=timezone.utc,
        )
        super().__init__(from_datetime=from_datetime, to_datetime=to_datetime)

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        today_datetime = datetime.now(timezone.utc)
        mondays_this_month = [
            (today_datetime.replace(day=1) + timedelta(days=i)).date()
            for i in range(today_datetime.day)
            if (today_datetime.replace(day=1) + timedelta(days=i)).weekday() == 0
        ]
        self.week_index = len(mondays_this_month)
        if today_datetime.replace(day=1).weekday() != 0:
            self.week_index += 1
        chunks = []
        for week_index, week in enumerate(mondays_this_month, start=1):
            week_start = today_datetime.replace(
                day=week.day, hour=0, minute=0, second=0, microsecond=0
            )
            week_end = (week_start + timedelta(days=6)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            files = [
                file
                for file in self.files
                if week_start <= file["LastModified"] <= week_end
            ]
            if not files:
                continue
            chunk = FileBatch(
                manifest_data=BatchManifestItem(
                    name=f"{week_start.strftime('%B %Y')} (week {week_index})",
                    file=f"{self.export_prefix}/{today_datetime.strftime('%Y-%m')}-w{week_index}.zip"
                    if self.export_prefix
                    else f"{today_datetime.strftime('%Y-%m')}-w{week_index}.zip",
                    total_size=sum(file["Size"] for file in files),
                    file_count=len(files),
                    created_timestamp=datetime.now(timezone.utc),
                    from_datetime=week_start,
                    to_datetime=week_end,
                ),
                files=files,
            )
            chunks.append(chunk)
        return chunks

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime >= self.from_datetime
            and item.to_datetime <= self.to_datetime
        ]


class LastMonthPackager(Packager):
    packager_name = "last_month"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        if today_datetime.month == 1:
            return  # Skip processing for January as there is no last month in the same year
        last_month = today_datetime.replace(day=1) + timedelta(days=-1)
        from_datetime = datetime(
            last_month.year, last_month.month, 1, 0, 0, 0, 0, tzinfo=timezone.utc
        )
        to_datetime = datetime(
            last_month.year,
            last_month.month,
            last_month.day,
            23,
            59,
            59,
            999999,
            tzinfo=timezone.utc,
        )
        export_filename = f"{last_month.strftime('%Y-%m')}.zip"
        self.name = f"{last_month.strftime('%B %Y')}"
        super().__init__(
            export_filename=export_filename,
            from_datetime=from_datetime,
            to_datetime=to_datetime,
        )

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunk = FileBatch(
            manifest_data=BatchManifestItem(
                name=self.name,
                file=f"{self.export_prefix}/{self.export_filename}"
                if self.export_prefix
                else self.export_filename,
                total_size=sum(file["Size"] for file in self.files),
                file_count=len(self.files),
                created_timestamp=datetime.now(timezone.utc),
                from_datetime=self.from_datetime,
                to_datetime=self.to_datetime,
            ),
            files=self.files,
        )
        return [chunk]

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime >= self.from_datetime
            and item.to_datetime <= self.to_datetime
        ]


class ThisMonthPackager(Packager):
    packager_name = "this_month"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        from_datetime = today_datetime.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        to_datetime = (from_datetime + timedelta(days=32)).replace(day=1) - timedelta(
            microseconds=1
        )
        export_filename = f"{from_datetime.strftime('%Y-%m')}.zip"
        self.name = f"{from_datetime.strftime('%B %Y')}"
        super().__init__(
            export_filename=export_filename,
            from_datetime=from_datetime,
            to_datetime=to_datetime,
        )

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunk = FileBatch(
            manifest_data=BatchManifestItem(
                name=self.name,
                file=f"{self.export_prefix}/{self.export_filename}"
                if self.export_prefix
                else self.export_filename,
                total_size=sum(file["Size"] for file in self.files),
                file_count=len(self.files),
                created_timestamp=datetime.now(timezone.utc),
                from_datetime=self.from_datetime,
                to_datetime=self.to_datetime,
            ),
            files=self.files,
        )
        return [chunk]

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime >= self.from_datetime
            and item.to_datetime <= self.to_datetime
        ]


class AllMonthsThisYearPackager(Packager):
    packager_name = "all_months_this_year"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        from_datetime = today_datetime.replace(
            day=1, month=1, hour=0, minute=0, second=0, microsecond=0
        )
        to_datetime = from_datetime.replace(
            month=12, day=31, hour=23, minute=59, second=59, microsecond=999999
        )
        super().__init__(from_datetime=from_datetime, to_datetime=to_datetime)

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunks = []
        today_datetime = datetime.now(timezone.utc)
        for month in range(1, today_datetime.month):
            month_start = today_datetime.replace(
                day=1, month=month, hour=0, minute=0, second=0, microsecond=0
            )
            month_end = (month_start + timedelta(days=31)).replace(day=1) - timedelta(
                days=1
            )
            month_end = month_end.replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            files = [
                file
                for file in self.files
                if month_start <= file["LastModified"] <= month_end
            ]
            if not files:
                continue
            chunk = FileBatch(
                manifest_data=BatchManifestItem(
                    name=f"{month_start.strftime('%B %Y')}",
                    file=f"{self.export_prefix}/{month_start.strftime('%Y-%m')}.zip"
                    if self.export_prefix
                    else f"{month_start.strftime('%Y-%m')}.zip",
                    total_size=sum(file["Size"] for file in files),
                    file_count=len(files),
                    created_timestamp=datetime.now(timezone.utc),
                    from_datetime=month_start,
                    to_datetime=month_end,
                ),
                files=files,
            )
            chunks.append(chunk)
        return chunks

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime >= self.from_datetime
            and item.to_datetime <= self.to_datetime
        ]


class LastYearPackager(Packager):
    packager_name = "last_year"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        today_year = today_datetime.year
        yesteryear = today_year - 1
        from_datetime = datetime(yesteryear, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        to_datetime = datetime(
            yesteryear, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc
        )
        export_filename = f"{yesteryear}.zip"
        self.name = str(yesteryear)
        super().__init__(
            export_filename=export_filename,
            from_datetime=from_datetime,
            to_datetime=to_datetime,
        )

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunk = FileBatch(
            manifest_data=BatchManifestItem(
                name=self.name,
                file=f"{self.export_prefix}/{self.export_filename}"
                if self.export_prefix
                else self.export_filename,
                total_size=sum(file["Size"] for file in self.files),
                file_count=len(self.files),
                created_timestamp=datetime.now(timezone.utc),
                from_datetime=self.from_datetime,
                to_datetime=self.to_datetime,
            ),
            files=self.files,
        )
        return [chunk]

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime >= self.from_datetime
            and item.to_datetime <= self.to_datetime
        ]


class ThisYearPackager(Packager):
    packager_name = "this_year"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        from_datetime = today_datetime.replace(
            day=1, month=1, hour=0, minute=0, second=0, microsecond=0
        )
        to_datetime = today_datetime.replace(
            day=31, month=12, hour=23, minute=59, second=59, microsecond=999999
        )
        export_filename = f"{today_datetime.year}.zip"
        self.name = str(today_datetime.year)
        super().__init__(
            export_filename=export_filename,
            from_datetime=from_datetime,
            to_datetime=to_datetime,
        )

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunk = FileBatch(
            manifest_data=BatchManifestItem(
                name=self.name,
                file=f"{self.export_prefix}/{self.export_filename}"
                if self.export_prefix
                else self.export_filename,
                total_size=sum(file["Size"] for file in self.files),
                file_count=len(self.files),
                created_timestamp=datetime.now(timezone.utc),
                from_datetime=self.from_datetime,
                to_datetime=self.to_datetime,
            ),
            files=self.files,
        )
        return [chunk]

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.from_datetime >= self.from_datetime
            and item.to_datetime <= self.to_datetime
        ]


class AllPreviousYearsPackager(Packager):
    packager_name = "all_previous_years"
    packager_group = "by_date"

    def __init__(self):
        today_datetime = datetime.now(timezone.utc)
        to_datetime = today_datetime.replace(day=1, month=1) - timedelta(days=1)
        super().__init__(to_datetime=to_datetime)

    def _chunk(self) -> list[FileBatch]:
        logger.info("Chunking files")
        chunks = []
        this_year = datetime.now(timezone.utc).year
        years = set(file["LastModified"].year for file in self.files)
        years = [year for year in years if year < this_year]
        for year in sorted(years, reverse=True):
            if year >= this_year:
                continue
            logger.debug(f"Processing year: {year}")
            year_start = datetime(year, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
            year_end = datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
            files = [
                file
                for file in self.files
                if year_start <= file["LastModified"] <= year_end
            ]
            if not files:
                continue
            chunk = FileBatch(
                manifest_data=BatchManifestItem(
                    name=f"{year_start.strftime('%Y')}",
                    file=f"{self.export_prefix}/{year_start.strftime('%Y')}.zip"
                    if self.export_prefix
                    else f"{year_start.strftime('%Y')}.zip",
                    total_size=sum(file["Size"] for file in files),
                    file_count=len(files),
                    created_timestamp=datetime.now(timezone.utc),
                    from_datetime=year_start,
                    to_datetime=year_end,
                ),
                files=files,
            )
            chunks.append(chunk)
        return chunks

    def _manifest_items_to_remove(
        self, existing_manifest_items: list[BatchManifestItem]
    ) -> list[BatchManifestItem]:
        return [
            item
            for item in existing_manifest_items
            if item.to_datetime <= self.to_datetime
        ]


class AllPackager(Packager):
    packager_name = "all"
    packager_group = "all"


class ChunkedPackager(AllPackager):
    packager_name = "chunked"
    packager_group = "chunked"

    def __init__(self, *args, **kwargs):
        self.chunk_size = int(args[0]) if args else 1000
        super().__init__()

    def _chunk(self) -> list[FileBatch]:
        logger.info(f"Chunking files into chunks of {self.chunk_size} files")
        file_chunks = [
            self.files[i : i + self.chunk_size]
            for i in range(0, len(self.files), self.chunk_size)
        ]
        return [
            FileBatch(
                manifest_data=BatchManifestItem(
                    name=f"Batch {(i + 1)} of {len(file_chunks)}",
                    file=f"{self.export_prefix}/all_{f'{(i + 1):04}'}.zip"
                    if self.export_prefix
                    else f"all_{f'{(i + 1):04}'}.zip",
                    total_size=sum(file["Size"] for file in chunk),
                    file_count=len(chunk),
                    created_timestamp=datetime.now(timezone.utc),
                    from_datetime=self.from_datetime,
                    to_datetime=self.to_datetime,
                ),
                files=chunk,
            )
            for i, chunk in enumerate(file_chunks)
        ]


class SizedPackager(AllPackager):
    packager_name = "sized"
    packager_group = "sized"

    def __init__(self, *args, **kwargs):
        self.chunk_size = int(args[0]) if args else 100000
        super().__init__()

    def _chunk(self) -> list[FileBatch]:
        logger.info(f"Chunking files into chunks of file size {self.chunk_size} bytes")
        file_chunks = []
        current_chunk = []
        current_sum = 0
        for file in self.files:
            size = file["Size"]
            if current_sum + size <= self.chunk_size or (
                not current_chunk and size > self.chunk_size
            ):
                current_chunk.append(file)
                current_sum += size
            else:
                file_chunks.append(current_chunk)
                current_chunk = [file]
                current_sum = size
        if current_chunk:
            file_chunks.append(current_chunk)
        return [
            FileBatch(
                manifest_data=BatchManifestItem(
                    name=f"Batch {(i + 1)} of {len(file_chunks)}",
                    file=f"{self.export_prefix}/all_{f'{(i + 1):04}'}.zip"
                    if self.export_prefix
                    else f"all_{f'{(i + 1):04}'}.zip",
                    total_size=sum(file["Size"] for file in chunk),
                    file_count=len(chunk),
                    created_timestamp=datetime.now(timezone.utc),
                    from_datetime=self.from_datetime,
                    to_datetime=self.to_datetime,
                ),
                files=chunk,
            )
            for i, chunk in enumerate(file_chunks)
        ]


class Batch:
    packager_class = None
    source = None
    manifest_name = None
    prefix = None

    def __init__(self, packager_class: type[Packager], extra_args: list[str] = None):
        self.packager_class = packager_class
        self.extra_args = extra_args or []

    def process(self) -> None:
        if not self.source:
            raise ValueError("No source has been defined for this batch.")
        if not isinstance(self.source, tuple):
            raise ValueError("Source must be a tuple of (bucket_name, prefix).")
        if not self.source[0]:
            raise ValueError("Source bucket name must be provided.")
        if not self.manifest_name:
            raise ValueError("No manifest_name has been defined for this batch.")
        if not self.prefix:
            raise ValueError("No prefix has been defined for this batch.")
        packager = self.packager_class(*self.extra_args)
        packager.scan(self.source)
        packager.process(manifest_name=self.manifest_name, export_prefix=self.prefix)


class MerlinBatch(Batch):
    source = (
        os.environ.get("S3_SOURCE_BUCKET_MERLIN", ""),
        os.environ.get("S3_SOURCE_PREFIX_MERLIN", ""),
    )
    manifest_name = os.environ.get("S3_MANIFEST_NAME", "manifest.json")
    prefix = os.environ.get("S3_EXPORT_PREFIX_MERLIN", "merlin")


batches = {
    "merlin": MerlinBatch,
}
packagers = {
    ThisWeekPackager.packager_name: ThisWeekPackager,
    ThisMonthPackager.packager_name: ThisMonthPackager,
    ThisYearPackager.packager_name: ThisYearPackager,
    LastMonthPackager.packager_name: LastMonthPackager,
    LastYearPackager.packager_name: LastYearPackager,
    AllWeeksThisMonthPackager.packager_name: AllWeeksThisMonthPackager,
    AllMonthsThisYearPackager.packager_name: AllMonthsThisYearPackager,
    AllPreviousYearsPackager.packager_name: AllPreviousYearsPackager,
    AllPackager.packager_name: AllPackager,
    ChunkedPackager.packager_name: ChunkedPackager,
    SizedPackager.packager_name: SizedPackager,
}
all_timed_packagers_name = "all_year_month_week"


def main(batch, packager, extra_args) -> None:
    logger.info(
        f"Processing batch: {batch} with packager: {packager} and extra_args: {extra_args}"
    )
    batch_class = batches[batch]
    batches_to_process = []
    if packager == all_timed_packagers_name:
        timed_packagers = [
            AllPreviousYearsPackager,
            AllMonthsThisYearPackager,
            AllWeeksThisMonthPackager,
        ]
        batches_to_process = [
            batch_class(packager_class=packager_class, extra_args=extra_args)
            for packager_class in timed_packagers
        ]
    else:
        batches_to_process = [
            batch_class(packager_class=packagers[packager], extra_args=extra_args)
        ]
    for batch_to_process in batches_to_process:
        batch_to_process.process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "batch", help="The batch to process", choices=list(batches.keys())
    )
    parser.add_argument(
        "packager",
        help="The packager to use",
        choices=list(packagers.keys()) + [all_timed_packagers_name],
    )
    parser.add_argument(
        "options", nargs="*", help="Additional options for the packager"
    )
    args = parser.parse_args()
    main(args.batch, args.packager, args.options)


def lambda_handler(event, context):
    if "Batch" not in event:
        raise ValueError("Event must contain 'Batch' key.")
    if "Packager" not in event:
        raise ValueError("Event must contain 'Packager' key.")
    batch = event["Batch"]
    packager = event["Packager"]
    options = event.get("Options", [])
    if not isinstance(options, list):
        raise ValueError("'Options' must be a list if provided.")
    main(batch, packager, options)
