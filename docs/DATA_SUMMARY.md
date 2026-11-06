# Data summary

_Generated 2026-11-05 by `python -m pipeline docs`._

**1,100,401 unified occurrence records** (2,327,043 cleaned records in, 1,226,642 cross-source duplicates removed: 1,213,219 exact-ID, 13,423 fuzzy).

### Records per species and source

| species_scientific | gbif | obis | seamap | whalinghistory | total |
|---|---:|---:|---:|---:|---:|
| Balaenoptera musculus | 15,386 | 11,996 | 2,445 | 230 | 30,057 |
| Balaenoptera physalus | 203,439 | 83,272 | 2,426 | 3,867 | 293,004 |
| Eubalaena australis | 12,676 | 16,948 | 505 | 9,615 | 39,744 |
| Eubalaena glacialis | 3,287 | 19,763 | 31 | 8 | 23,089 |
| Megaptera novaeangliae | 53,548 | 169,151 | 333,184 | 2,306 | 558,189 |
| Physeter macrocephalus | 31,465 | 91,814 | 8,660 | 24,379 | 156,318 |

### Records per species and era

| species_scientific | ≤1910s | 1920–1970s | 1980–90s | 2000s | 2010s | 2020s |
|---|---:|---:|---:|---:|---:|---:|
| Balaenoptera musculus | 425 | 7,706 | 5,845 | 5,221 | 6,210 | 4,650 |
| Balaenoptera physalus | 6,784 | 49,176 | 10,977 | 12,856 | 111,329 | 101,882 |
| Eubalaena australis | 15,209 | 103 | 7,169 | 4,110 | 7,154 | 5,999 |
| Eubalaena glacialis | 71 | 433 | 861 | 7,225 | 8,937 | 5,562 |
| Megaptera novaeangliae | 4,530 | 29,160 | 27,877 | 75,425 | 172,277 | 248,920 |
| Physeter macrocephalus | 46,087 | 32,124 | 5,267 | 25,732 | 38,684 | 8,424 |

<details><summary>Records per decade and source</summary>

| decade | gbif | obis | seamap | whalinghistory | total |
|---:|---:|---:|---:|---:|---:|
| 1720 | 1 | 0 | 0 | 0 | 1 |
| 1730 | 1 | 0 | 0 | 0 | 1 |
| 1750 | 1 | 15 | 0 | 0 | 16 |
| 1760 | 15 | 19 | 0 | 0 | 34 |
| 1770 | 0 | 4 | 0 | 0 | 4 |
| 1780 | 2 | 1 | 0 | 10 | 13 |
| 1790 | 2 | 2 | 0 | 157 | 161 |
| 1800 | 1 | 0 | 0 | 107 | 108 |
| 1810 | 0 | 0 | 0 | 159 | 159 |
| 1820 | 2 | 2 | 0 | 2382 | 2386 |
| 1830 | 8 | 4 | 0 | 11881 | 11893 |
| 1840 | 10 | 29278 | 0 | 13179 | 42467 |
| 1850 | 8 | 3 | 0 | 3400 | 3411 |
| 1860 | 13 | 4 | 0 | 3172 | 3189 |
| 1870 | 13 | 1 | 0 | 1836 | 1850 |
| 1880 | 18 | 2 | 0 | 1404 | 1424 |
| 1890 | 11 | 6 | 0 | 1191 | 1208 |
| 1900 | 1272 | 285 | 0 | 1056 | 2613 |
| 1910 | 1558 | 153 | 0 | 457 | 2168 |
| 1920 | 275 | 13685 | 0 | 14 | 13974 |
| 1930 | 509 | 10354 | 0 | 0 | 10863 |
| 1940 | 2795 | 7815 | 0 | 0 | 10610 |
| 1950 | 417 | 33536 | 0 | 0 | 33953 |
| 1960 | 1883 | 22530 | 2 | 0 | 24415 |
| 1970 | 2656 | 21664 | 567 | 0 | 24887 |
| 1980 | 4674 | 10831 | 2717 | 0 | 18222 |
| 1990 | 14796 | 15608 | 9370 | 0 | 39774 |
| 2000 | 42816 | 48598 | 39155 | 0 | 130569 |
| 2010 | 134396 | 125762 | 84433 | 0 | 344591 |
| 2020 | 111648 | 52782 | 211007 | 0 | 375437 |

</details>

### Cleaning report (per source)

| source | rows in | dropped | kept | top drop reasons | top flags |
|---|---:|---:|---:|---|---|
| gbif | 964,817 | 9,101 | 955,716 | absence_record (8,430), on_land (549), date_before_1700 (78), taxon_unresolved (38) | tz_assumed_utc (705,549), acoustic (194,072), gbif:coordinate_rounded (126,548), on_land_within_tolerance (26,836) |
| obis | 985,113 | 1,556 | 983,557 | license_all_rights_reserved (939), on_land (562), date_before_1700 (55) | tz_assumed_utc (674,416), acoustic (193,928), date_range (81,502), date_from_year_month_day (36,217) |
| seamap | 348,369 | 1,004 | 347,365 | license_all_rights_reserved (939), on_land (58), non_target_species (7) | tz_assumed_utc (304,242), on_land_within_tolerance (7,258), license_all_rights_reserved (939), stranding (68) |
| whalinghistory | 468,249 | 427,844 | 40,405 | no_encounter_vessel_day (kept in effort table) (391,922), taxon_above_species (24,621), non_target_species (11,029), on_land (199) | species_inferred_from_region (16,245), whole_degree_position (1,367), on_land_within_tolerance (1,112), position_corrected (38) |

Full reports: `data/interim/reports/<source>.json`.
