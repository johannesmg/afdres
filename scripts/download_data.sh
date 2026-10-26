#!/usr/bin/env bash
# Download county-level (Kreis = NUTS-3) inputs from the GERDA database
# (Heddesheimer, Hilbig, Sichart & Wiedemann 2025, Scientific Data 12:618).
set -euo pipefail
dir="$(cd "$(dirname "$0")/.." && pwd)/data/raw"
mkdir -p "$dir"
base=https://media.githubusercontent.com/media/awiedem/german_election_data/main/data
for p in \
  federal_elections/county_level/final/federal_cty_harm.csv \
  covars_county/final/cty_area_pop_emp.csv \
  covars_county/cty_inkar_harm2021.csv; do
  echo "fetching $p"
  curl -sSfL "$base/$p" -o "$dir/$(basename "$p")"
done
