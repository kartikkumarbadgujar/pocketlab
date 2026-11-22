# Vercel serverless entry point: Vercel's Python runtime serves the ASGI app exported as `app`.
# Migrations and the catalog seed are not run here; run them once against the database (see docs/DEPLOY_VERCEL.md).
from app.main import app  # noqa: F401
