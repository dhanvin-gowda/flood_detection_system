
from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'


class Settings(BaseSettings):
    sentinel_hub_id: str = Field(default='', alias='SENTINEL_HUB_ID')
    sentinel_hub_api_key: str = Field(default='', alias='SENTINEL_HUB_API_KEY')

    # The credentials in .env authenticate against the Copernicus Data Space
    # Ecosystem (CDSE). The client id carries an "sh-" prefix, which looks like a
    # Sentinel Hub credential, but Sentinel Hub's realm answers every request
    # with invalid_client while CDSE issues a token for the same pair.
    cdse_oauth_token_url: str = Field(
        default='https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token',
        alias='CDSE_OAUTH_TOKEN_URL',
    )
    cdse_stac_url: str = Field(
        default='https://stac.dataspace.copernicus.eu/v1',
        alias='CDSE_STAC_URL',
    )
    cdse_openEO_url: str = Field(
        default='https://openeo.dataspace.copernicus.eu/openeo/1.0',
        alias='CDSE_OPENEO_URL',
    )

    # Sentinel Hub's Process API. Kept for reference and optional override; the
    # current credentials cannot authenticate against it.
    sh_auth_url: str = Field(
        default='https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token',
        alias='SENTINEL_HUB_AUTH_URL',
    )
    sh_process_url: str = Field(
        default='https://services.sentinel-hub.com/api/v1/process',
        alias='SENTINEL_HUB_PROCESS_URL',
    )

    next_public_maptiler_key: str = Field(default='', alias='NEXT_PUBLIC_MAPTILER_KEY')
    next_public_mapbox_token: str = Field(default='', alias='NEXT_PUBLIC_MAPBOX_TOKEN')

    # OpenStreetMap Overpass. OSM is fetched alongside the Sentinel path, so a
    # slow or dead Overpass instance must never stall the analysis: the pipeline
    # joins these futures with osm_timeout_s and degrades per layer on failure.
    osm_overpass_url: str = Field(
        default='https://overpass-api.de/api/interpreter',
        alias='OSM_OVERPASS_URL',
    )
    osm_timeout_s: float = Field(default=90.0, alias='OSM_TIMEOUT_S')

    max_before_days: int = Field(default=60, alias='MAX_BEFORE_DAYS')
    max_after_days: int = Field(default=30, alias='MAX_AFTER_DAYS')
    # STAC page size. A 60-day before window returns ~55 features for a
    # mid-size AOI, which the previous hard-coded limit of 50 truncated.
    cdse_search_limit: int = Field(default=200, alias='CDSE_SEARCH_LIMIT')

    # Flood detection tuning. CDSE distributes Sentinel-1 GRDH as uncalibrated
    # uint16 amplitude (DN), not calibrated sigma0, so no absolute backscatter
    # threshold is portable between scenes. The classifier therefore works on the
    # log-amplitude difference and gates on a data-derived percentile.
    flood_threshold_db: float = Field(default=-3.0, alias='FLOOD_THRESHOLD_DB')
    flood_after_percentile: float = Field(default=35.0, alias='FLOOD_AFTER_PERCENTILE')

    class Config:
        env_file = '.env'
        populate_by_name = True
        # .env carries frontend-only keys with no Settings field; pydantic-settings
        # defaults to extra='forbid', which made every import of config.Settings raise.
        extra = 'ignore'


settings = Settings()