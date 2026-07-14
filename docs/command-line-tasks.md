# Command line tasks

## Process a batch

The process script can be found in `tasks/process.py`.

```sh
poetry run python tasks/process.py <batch> <packager> <options>
```

### Batches

- `merlin` - all files in the series `ES 38`

### Packagers

- `this_week` - create a ZIP of all files from this week, replacing any existing bundles for this week
- `this_month` - create a ZIP of all files from this month, replacing all weekly files for the month
- `this_year` - create a ZIP of all files from this year, replacing all weekly and monthly files for the year
- `last_month` - create a ZIP of all files from the previous month and remove any weekly ZIPs from that month
- `last_year` - create a ZIP of all files from the previous year and remove any monthly ZIPs from that year
- `all_weeks_this_month` - create multiple ZIPs, for every week this month
- `all_months_this_year` - create multiple ZIPs, for every month this year excluding the current month
- `all_previous_years` - create multiple ZIPs, for every year prior to the current year
- `all_year_month_week` - create all the year, month and week ZIPs from `all_previous_years`, `all_months_this_year` and `all_weeks_this_month`
- `all` - create a ZIP of all files
- `chunked` - create multiple ZIPs, chunked into a set size which can be set by passing a number into the `<options>` parameter of the command
- `sized` - create multiple ZIPs, chunked into a target file size which can be set by passing a number into the `<options>` parameter of the command (the file size of the chunk in bytes before writing to a ZIP file)

## Merlin

- Run `last_year` at 01:00 on the first day of the year (cron `0 1 1 1 *` for `poetry run python tasks/process.py merlin last_year`)
- Run `last_month` at 02:00 on the first day of the month (cron `0 2 1 * *` for `poetry run python tasks/process.py merlin last_month`)
- Run `this_week` at 03:00 every day (cron `0 3 * * *` for `poetry run python tasks/process.py merlin this_week`)
