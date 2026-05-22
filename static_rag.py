from dataclasses import dataclass

from config import QDRANT_COLLECTION
from vector_db import client_qrant, get_embedding


@dataclass(frozen=True)
class StaticDocument:
    score: float
    source_id: str
    title: str | None
    text: str
    payload: dict


def retrieve_static_context(query: str, limit: int = 5) -> list[StaticDocument]:
    vector = get_embedding(query)
    result = client_qrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        limit=limit,
        with_payload=True,
    )

    documents: list[StaticDocument] = []
    for point in result.points:
        payload = point.payload or {}
        documents.append(
            StaticDocument(
                score=point.score,
                source_id=str(payload.get("source_id", point.id)),
                title=payload.get("title") or payload.get("term"),
                text=str(payload.get("text", "")),
                payload=payload,
            )
        )
    return documents


def format_static_context(documents: list[StaticDocument]) -> str:
    if not documents:
        return "STATIC_CONTEXT_EMPTY"

    chunks = []
    for index, doc in enumerate(documents, start=1):
        label = doc.title or doc.source_id
        chunks.append(
            f"[{index}] source_id={doc.source_id}; score={doc.score:.4f}; title={label}\n{doc.text}"
        )
    return "\n\n".join(chunks)
