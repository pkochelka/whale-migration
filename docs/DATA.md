# Data reference

What the pipeline produces, what every column means, and every cleaning rule it applies.
For record counts see [DATA_SUMMARY.md](DATA_SUMMARY.md); for licenses and citations see [SOURCES.md](SOURCES.md).

## Output files

All of these live in `data/` (git-ignored, ~3 GB). Rebuild them with `uv run python -m pipeline run`.

| path | content |
|---|---|
| `data/processed/occurrences.parquet` | **all whale records**, one row per de-duplicated record, GeoParquet (EPSG:4326 points) |
| `data/processed/effort_logbook.parquet` | every 1784–1920 whaling vessel-day, including days with no whales (= search effort) |
| `data/processed/duplicates.parquet` | every removed duplicate → the record it was merged into |
| `data/processed/whales.duckdb` | DuckDB views over the parquet files (open it from the project root) |
| `data/processed/env/sst_monthly.nc` | sea surface temperature (HadISST), 1°, monthly 1870–present |
| `data/processed/env/chlorophyll_monthly.nc` | chlorophyll-a (ESA OC-CCI), 1°, monthly 1997–present |
| `data/processed/env/sea_ice_concentration_monthly.nc` | sea-ice concentration (NSIDC CDR v6), 1°, monthly 1978–present |
| `data/processed/env/sea_ice_extent_monthly.csv` | NSIDC Sea Ice Index extent per hemisphere |
| `data/processed/env/ship_density.nc` | AIS ship density 2015–2021, 0.1° |
| `data/processed/env/marine_protected_areas.parquet` | marine protected area polygons (WDPA), GeoParquet |
| `data/interim/<source>.parquet` | each source cleaned into the schema below, before de-duplication |
| `data/interim/reports/<source>.json` | cleaning report: rows in, dropped and flagged, with reasons |
| `data/raw/<source>/` | untouched downloads, each with a `.manifest.json` (URL, date, checksum, license) |

All grids share one convention: `time` = first of month (UTC), `lat` ascending, `lon` from −180 to 180.

## Occurrence columns

| column | meaning |
|---|---|
| `record_id` | unique id, `<source>:<id in that source>` |
| `source` | `whalinghistory`, `obis`, `gbif`, `seamap` or `movebank` |
| `dataset_id` | dataset inside the source, e.g. `AOWL:Maury`, `obis:<uuid>`, `gbif:<datasetKey>`, `seamap:1765` |
| `species_scientific`, `species_common`, `worms_aphia_id` | accepted species name (WoRMS) and its id |
| `individual_id` | a known individual whale, e.g. `happywhale:13734` (empty if unknown) |
| `event_type` | `catch` (whaling), `encounter` (photo-ID of a known whale), `sighting`, `tag_fix` (satellite tag) |
| `timestamp_utc` | **start** of the period the source states |
| `date_precision` | `day`, `month` or `year`: how much of the timestamp is real |
| `lat`, `lon` | position, lon from −180 to 180 (`pipeline.geo.to_0_360` converts) |
| `position_quality` | `logbook`, `gazetteer` (from a place name), `gps`, `argos`, `reported` |
| `count` | number of animals (logbook catches: whales struck) |
| `platform` | ship name and rig for logbooks, survey protocol or tag type otherwise |
| `license`, `citation` | per record, never empty |
| `raw_ref` | `<raw file>#<row key>`, a pointer back to the raw row |
| `occurrence_id` | original Darwin Core occurrenceID |
| `coord_uncertainty_m` | stated by the source, or estimated for logbooks (10 km noon fix, 25 km place name, 55 km whole degree) |
| `quality_flags` | `;`-separated notes, e.g. `on_land_within_tolerance;tz_assumed_utc` |
| `merged_sources` | every source this record appeared in before de-duplication |
| `n_duplicates` | how many copies were merged into this row |
| `dist_coast_km` | distance to the nearest coastline |

Useful flags to filter on: `acoustic` (hydrophone detection, not a visual sighting), `stranding`,
`position_corrected` (logbook typo fixed), `species_inferred_from_region` (logbook "Right" whale),
`tz_assumed_utc` (time without a time zone).

## Cleaning rules

**Species names.** Resolved against WoRMS (cached in `data/interim/worms_cache.json`): synonyms
become the accepted name and subspecies roll up to species. Vague names ("whale", *Cetacea*,
*Eubalaena*) are dropped by default. Names WoRMS maps to two different species are never guessed;
`taxonomy.overrides` in `config.yaml` resolves them explicitly.

**Positions.** Dropped: missing, latitude beyond ±90, longitude outside −180…360, and (0, 0).
Longitudes given as 0…360 are converted. A point on land is dropped only if it lies more than
max(5 km, its stated uncertainty) inland; nearer ones are kept and flagged, because coastal
sightings with rounded coordinates often fall just onshore.

**Dates.** Accepts ISO dates and times (with time zones), `YYYY-MM`, `YYYY`, ranges `A/B` (reduced to
the day, month or year both ends share; wider ranges are dropped) and unambiguous `DD/MM/YYYY`.
Ambiguous dates fall back to year precision. A day or month is never invented.

**Whaling logbooks (AOWL).** Positions are noon fixes copied by hand, so typos are common. The
pipeline follows each voyage and looks for a day that is impossibly far from both the day before and
the day after (> 450 km/day). It then tries to explain the jump as a flipped hemisphere, swapped
digits or one wrong digit, and corrects the position only when exactly one explanation fits. The
original position stays in `raw_ref`. "Right" whales are assigned to a species by ocean: southern
hemisphere → *E. australis*, North Atlantic → *E. glacialis*, North Pacific → *E. japonica*. Above
60°N whalers' "right whales" were often bowheads, so those stay at genus level and are dropped.
`Strike` becomes a `catch`, `Sight` a `sighting`; meetings with other ships (`Spoke`) are dropped.

**OBIS, GBIF and OBIS-SEAMAP.** Absence records and fossils are dropped. `event_type` comes from
the dataset: photo-ID with a known individual → `encounter`; tag/telemetry datasets → `tag_fix`;
whaling or catch datasets → `catch`; everything else → `sighting`. Records whose photographer marked
them "No use without permission" are removed (`licenses.drop_all_rights_reserved` in `config.yaml`).

**Tags (Movebank).** Low-quality Argos positions (classes 0, A, B, Z) are removed and impossible
speeds (> 15 km/h) flagged. Movebank needs `MOVEBANK_USERNAME` and `MOVEBANK_PASSWORD`; without them
it is skipped.

**Duplicates.** The same sighting often appears in OBIS, GBIF and SEAMAP. Records are merged when
they share species, original occurrenceID and day within 25 km, or, between different sources only,
species and day within 2 km. The most detailed copy is kept and filled in from the others
(`unify/dedup.py`).

## Source notes

| source | how it is fetched | note |
|---|---|---|
| WhalingHistory.org | zip download | the 2025 release has no position-type column |
| OBIS | `pyobis`, 10-year windows per species | records without a date are not reachable |
| GBIF | REST API, split so each query has ≤ 9 000 records (deeper pages time out) | set `GBIF_USERNAME`, `GBIF_PASSWORD`, `GBIF_EMAIL` to use bulk downloads instead |
| OBIS-SEAMAP / Happywhale | Darwin Core archives from Duke's IPT server | the SEAMAP website has no download links |
| Movebank | REST API | needs an account; skipped otherwise |
| NOAA ERDDAP | `erddapy` | the server is sometimes briefly offline; re-run the fetch to fill gaps |
| NSIDC, WDPA, shipping density | direct downloads | WDPA must not be redistributed |

## Known limitations

- Records without a year cannot be fetched from OBIS or GBIF with the date-split queries. They would be
  dropped anyway; each species' manifest lists reported vs fetched counts.
- Logbook errors that last several days are not detected as jumps; the land check removes the ones that end inland.
- Times without a time zone are assumed UTC (flag `tz_assumed_utc`).
- Environmental grids are coarse (1°) on purpose; lower `stride`/`coarsen` in `config.yaml` for regional work.
