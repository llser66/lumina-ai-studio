import asyncio
import contextlib
import logging
import uuid

from server.db import TERMINAL
from server.provider import ProviderFailure

logger = logging.getLogger("lumina.worker")


class Worker:
    def __init__(self, db, graph, outputs, provider=None):
        self.db, self.graph, self.outputs = db, graph, outputs
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
                    from PIL import Image

                    with Image.open(snapshot.values["output_path"]) as image:
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
            if row["status"] not in TERMINAL:
                if (self.outputs / f"{job['id']}.png").exists():
                    self.db.recover(job["id"])
                else:
                    self.db.fail(
                        job["id"],
                        "workflow_error",
                        "任务执行中断，请联系管理员核对。",
                        uncertain=bool(row["provider_started_at"]),
                    )
            # Never log exception strings: providers may include prompts or credentials.
            logger.error("job_id=%s exception_type=%s", job["id"], type(exc).__name__)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if self.provider and hasattr(self.provider, "cleanup_job"):
                await self.provider.cleanup_job(job["id"])

    async def run(self):
        while True:
            self.db.recover_expired(self.outputs)
            job = self.db.claim(self.owner)
            if job:
                await self.process(job)
            else:
                await asyncio.sleep(0.5)
