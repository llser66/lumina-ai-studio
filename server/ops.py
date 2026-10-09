"""Offline operator reconciliation. No network requests or automatic paid retries."""

import argparse
import time
import uuid
from pathlib import Path

from server.asset_store import create_asset_store
from server.config import Settings
from server.db import Database
from server.provider import normalize_image


def settle(settings, job_id, note, *, image=None, confirmed_no_image=False):
    db = Database(
        settings.database_url.get_secret_value().strip()
        or settings.data_dir / "lumina.sqlite3"
    )
    assets = create_asset_store(settings)
    job_id = str(uuid.UUID(job_id))
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job or job["status"] != "uncertain":
        raise ValueError("Only uncertain jobs can be reconciled")
    if image:
        content, width, height = normalize_image(Path(image).read_bytes())
        output = assets.output_key(job["user_id"], job_id)
        assets.put(output, content)
        db.complete(job_id, output, width, height)
    elif confirmed_no_image:
        db.fail(
            job_id, "operator_confirmed_no_image", "管理员已确认未产生可用图片，创作额度已释放。"
        )
    else:
        raise ValueError("An image or confirmed no-image outcome is required")
    db.execute(
        "INSERT INTO events(job_id,stage,message,created_at) VALUES(?,?,?,?)",
        (job_id, "reconciled", "管理员核对记录：" + note[:200], time.time()),
    )
    if hasattr(assets, "close"):
        assets.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", type=uuid.UUID)
    parser.add_argument("--note", required=True, help="A non-sensitive audit note")
    outcomes = parser.add_mutually_exclusive_group(required=True)
    outcomes.add_argument("--image", type=Path, help="Verified image retrieved from the provider")
    outcomes.add_argument(
        "--confirmed-no-image",
        action="store_true",
        help="Only after manually verifying the provider outcome",
    )
    arguments = parser.parse_args()
    settle(
        Settings(),
        arguments.job_id,
        arguments.note,
        image=arguments.image,
        confirmed_no_image=arguments.confirmed_no_image,
    )
    print("Job reconciled; no provider API was called.")


if __name__ == "__main__":
    main()
