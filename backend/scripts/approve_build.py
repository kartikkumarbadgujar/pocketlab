"""Operator-only approval of a quarantined APK build for execution.

There is deliberately no HTTP endpoint for this: a user must not be able to approve their own upload.
For the hackathon, approve only your own team's APKs or explicitly authorized sample applications.

    python -m scripts.approve_build 12 --reason "Team-owned demo app, built from commit abc123"
    python -m scripts.approve_build 12 --reject --reason "Not authorized"
"""
import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models import AppBuild, AuditLog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("build_id", type=int)
    parser.add_argument("--reason", required=True, help="Why this APK is authorized for execution")
    parser.add_argument("--reject", action="store_true")
    parser.add_argument("--remote-storage", action="store_true",
                        help="The APK lives on the hosted API's disk (e.g. a hosted server), not this machine: approve from the database "
                             "record only. The execution worker still re-verifies the SHA-256 before installing.")
    args = parser.parse_args()
    with SessionLocal() as db:
        build = db.get(AppBuild, args.build_id)
        if not build:
            print(f"Build {args.build_id} not found", file=sys.stderr)
            return 1
        digest = build.apk_sha256
        if not args.remote_storage:
            path = (Path(settings.upload_dir).resolve() / build.apk_storage_key).resolve()
            if not path.is_file():
                print(f"APK file missing from storage: {build.apk_storage_key} (use --remote-storage for a hosted API)", file=sys.stderr)
                return 1
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != build.apk_sha256:
                print("Stored APK hash does not match the upload record; refusing to approve", file=sys.stderr)
                return 1
            if not zipfile.is_zipfile(path):
                print("Stored file is not a ZIP/APK", file=sys.stderr)
                return 1
        build.scan_status = "rejected" if args.reject else "approved"
        db.add(AuditLog(actor_user_id=None, action="build.reject" if args.reject else "build.approve", resource_type="app_build",
                        resource_id=str(build.id), outcome=f"operator: {args.reason[:80]}"))
        db.commit()
        print(f"Build {build.id} ({build.package_name}, sha256 {digest[:12]}...) -> {build.scan_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
