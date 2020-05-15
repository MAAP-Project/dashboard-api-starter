"""covid_api api."""

from fastapi import APIRouter

from covid_api.api.api_v1.endpoints import tiles, metadata, ogc, timelapse, datasets

api_router = APIRouter()
api_router.include_router(tiles.router, tags=["tiles"])
api_router.include_router(metadata.router, tags=["metadata"])
api_router.include_router(ogc.router, tags=["OGC"])
api_router.include_router(timelapse.router, tags=["timelapse"])
api_router.include_router(datasets.router, tags=["datasets"])
