# platform/main.py
#
# Mural Content Platform — FastAPI application entry point
# Copyright (C) 2024  Mural Contributors
# MIT License — see platform/LICENSE

"""Mural content platform REST API.

Run locally::

    cd platform
    uvicorn main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from platform.routes.wallpapers import router as wallpapers_router
from platform.routes.upload import router as upload_router
from platform.routes.search import router as search_router

app = FastAPI(
    title="Mural Content Platform",
    description="Community wallpaper library for the Mural animated wallpaper platform.",
    version="0.1.0",
    license_info={"name": "MIT"},
)

# Allow the Mural desktop client to reach the API from any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(wallpapers_router, prefix="/wallpapers", tags=["wallpapers"])
app.include_router(upload_router,     prefix="/upload",     tags=["upload"])
app.include_router(search_router,     prefix="/search",     tags=["search"])


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": "Mural Content Platform", "version": "0.1.0", "docs": "/docs"}


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Health check endpoint used by load balancers and uptime monitors."""
    return {"status": "ok"}
