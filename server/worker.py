import asyncio
import contextlib
import io
import logging
import uuid

from PIL import Image

from server.asset_store import LocalAssetStore
from server.db import TERMINAL
from server.provider import ProviderFailure

logger = logging.getLogger("lumina.worker")


class Worker:
    def __init__(self, db, graph, outputs, provider=None, asset_store=None):
        self.db, self.graph, self.outputs = db, graph, outputs
        self.asset_store = asset_store or LocalAssetStore(outputs.parent)
        self.owner = str(uuid.uuid4())
        self.provider = provider

    async def heartbeat(self, job_id):
        while True:
            await asyncio.sleep(10)
            self.db.heartbeat(job_id, self.owner)

    async def process(self, job):
        config = {"configurable": {"thread_id": job["id"]}, "recursion_limit": 12}
        heartbeat = asyncio.create_task(self.heartbeat(job["id"]))
        try:
            snapshot = await self.graph.aget_state(config)
            if snapshot.values:
                if snapshot.next:
                    await self.graph.ainvoke(None, config, durability="sync")
                elif snapshot.values.get("output_path"):
                    with Image.open(io.BytesIO(self.asset_store.read(snapshot.values["output_path"]))) as image:
                        self.db.complete(job["id"], snapshot.values["output_path"], *image.size)
            else:
                await self.graph.ainvoke({"job_id": job["id"]}, config, durability="sync")
        except ProviderFailure as exc:
            self.db.fail(job["id"], exc.code, exc.message, uncertain=exc.uncertain)
            logger.warning("job_id=%s error_code=%s", job["id"], exc.code)
        except asyncio.CancelledError:
            # Leave the lease to expire. Recovery examines the durable image artifact.
            raise
        except Exception as exc:
            row = self.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
            storage_probe_failed = False
            if row and row["status"] not in TERMINAL:
                try:
                    output_key = self.asset_store.output_key(job["user_id"], job["id"])
                    output_exists = self.asset_store.exists(output_key)
                except Exception:
                    # Error handling must not fail a second time when remote
                    # storage is unavailable.  Before a billable provider call
                    # we can safely fail and let the user retry; afterwards we
                    # must keep the result uncertain to avoid duplicate billing.
                    output_exists = False
                    storage_probe_failed = True
                if output_exists:
                    self.db.recover(job["id"])
                elif row["provider_started_at"]:
                    self.db.fail(
                        job["id"],
                        "provider_unknown",
                        "连接中断，供应商结果待确认。为避免重复计费，此任务不会自动重试。",
                        uncertain=True,
                    )
                else:
                    self.db.fail(
                        job["id"],
                        "workflow_error",
                        "任务执行中断，请检查模型连接后重试。",
                    )
            # Never log exception strings: providers may include prompts or credentials.
            logger.error(
                "job_id=%s exception_type=%s storage_probe_failed=%s",
                job["id"],
                type(exc).__name__,
                storage_probe_failed,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if self.provider and hasattr(self.provider, "cleanup_job"):
                await self.provider.cleanup_job(job["id"])

    async def run(self):
        while True:
            try:
                self.db.recover_expired(self.asset_store)
                job = self.db.claim(self.owner)
            except Exception as exc:
                logger.error("worker storage unavailable exception_type=%s", type(exc).__name__)
                await asyncio.sleep(2)
                continue
            if job:
                try:
                    await self.process(job)
                except Exception as exc:
                    logger.error("worker job recovery exception_type=%s", type(exc).__name__)
            else:
                await asyncio.sleep(0.5)
