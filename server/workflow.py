import asyncio
import io
import logging
import os
import time
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from PIL import Image

from server.asset_store import LocalAssetStore
from server.provider import ProviderFailure
from server.schemas import GenerationSpec

logger = logging.getLogger("lumina.workflow")
STYLE_GUIDES = {
    "natural": "Natural photorealism, authentic textures, believable light and balanced composition.",
    "editorial": "Premium editorial product photography, considered art direction, crisp material details.",
    "cinematic": "Cinematic composition, dramatic lighting, filmic color grading and atmosphere.",
    "3d": "High-end 3D render, sculptural forms, tactile materials and physically based lighting.",
    "illustration": "Expressive editorial illustration with deliberate shapes and a coherent color palette.",
    "anime": "Detailed anime illustration, confident linework and cinematic cel shading.",
}


class GenerationState(TypedDict, total=False):
    job_id: str
    effective_prompt: str
    output_path: str


def atomic_write(path: Path, content: bytes):
    temporary = path.with_suffix(".pending")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_graph(db, provider, outputs, checkpointer, asset_store=None):
    """Checkpoints contain references and prompts; image bytes never enter graph state."""
    asset_store = asset_store or LocalAssetStore(outputs.parent)
    outputs.mkdir(parents=True, exist_ok=True)

    def job(state):
        return db.one("SELECT * FROM jobs WHERE id=?", (state["job_id"],))

    def selected_provider(state):
        return provider.for_job(job(state)) if hasattr(provider, "for_job") else provider

    def stage(state, name, message, **fields):
        db.stage(state["job_id"], name, message, **fields)
        logger.info("job_id=%s stage=%s", state["job_id"], name)

    async def validate(state):
        row = job(state)
        GenerationSpec(**{key: row[key] for key in GenerationSpec.model_fields})
        stage(state, "validating", "正在整理创作参数")
        prompt = f"{row['prompt']}\nVisual direction: {STYLE_GUIDES[row['style']]}"
        if row["negative_prompt"]:
            prompt += f"\nAvoid: {row['negative_prompt']}"
        return {"effective_prompt": prompt}

    async def enhance(state):
        stage(state, "enhancing", "正在优化画面细节与构图")
        prompt = await selected_provider(state).enhance(state["effective_prompt"])
        stage(state, "enhancing", "创作描述已整理", enhanced_prompt=prompt)
        return {"effective_prompt": prompt}

    async def moderate(state):
        stage(state, "moderating", "正在检查创作内容")
        row = job(state)
        # Check both the user's original intent and the final provider prompt.
        if not await selected_provider(state).moderate(
            row["prompt"] + "\n" + state["effective_prompt"]
        ):
            raise ProviderFailure("content_blocked", "这次描述未通过内容检查，请换一种描述。")
        return {}

    async def generate(state):
        row = job(state)
        path = asset_store.output_key(row["user_id"], row["id"])
        if asset_store.exists(path):
            # A crash after durable object storage must never trigger another API call.
            return {"output_path": path}
        if row["provider_started_at"]:
            raise ProviderFailure(
                "provider_unknown", "供应商结果待确认，此任务不会自动重试。", uncertain=True
            )
        reference = None
        remove_reference = False
        if row["reference_id"]:
            asset = db.one(
                "SELECT * FROM assets WHERE id=? AND user_id=?",
                (row["reference_id"], row["user_id"]),
            )
            if not asset or not asset_store.exists(asset["path"]):
                raise ProviderFailure("reference_missing", "参考图片已不可用，请重新上传。")
            reference, remove_reference = asset_store.materialize(asset["path"], outputs)
        spec = GenerationSpec(**{key: row[key] for key in GenerationSpec.model_fields})
        stage(
            state,
            "generating",
            "正在生成画面，这可能需要一至三分钟",
            provider_started_at=time.time(),
        )
        try:
            result = await selected_provider(state).generate(
                spec, state["effective_prompt"], reference, row["model"]
            )
            await asyncio.to_thread(asset_store.put, path, result.content)
        finally:
            asset_store.cleanup_materialized(reference, remove_reference)
        stage(state, "saving", "原图已安全保存", provider_request_id=result.request_id)
        return {"output_path": path}

    async def persist(state):
        stage(state, "saving", "正在归档创作参数与原图")
        with Image.open(io.BytesIO(asset_store.read(state["output_path"]))) as image:
            width, height = image.size
        db.complete(state["job_id"], state["output_path"], width, height)
        return {}

    graph = StateGraph(GenerationState)
    for name, node in [
        ("validate", validate),
        ("enhance", enhance),
        ("moderate", moderate),
        ("generate", generate),
        ("persist", persist),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "validate")
    graph.add_conditional_edges(
        "validate",
        lambda state: "enhance" if job(state)["enhance"] else "moderate",
        {"enhance": "enhance", "moderate": "moderate"},
    )
    graph.add_edge("enhance", "moderate")
    graph.add_edge("moderate", "generate")
    graph.add_edge("generate", "persist")
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer)
