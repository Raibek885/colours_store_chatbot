import argparse
import ast
import json
import uuid
from collections import Counter
from pathlib import Path

from qdrant_client.models import PointStruct


DATA_DIR = Path(__file__).resolve().parent / "data"
EXPECTED_TOTAL = 28
QDRANT_COLLECTION = "colour_store"
UUID_NAMESPACE = uuid.UUID("2ab2a5ef-e395-4a59-8c92-1e3107d0e7c4")


def load_json_items(filename):
    path = DATA_DIR / filename
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return data
    return [data]


def load_python_variable(filename, variable_name):
    path = DATA_DIR / filename
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable_name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == variable_name:
                return ast.literal_eval(node.value)

    raise ValueError(f"{variable_name!r} was not found in {filename}")


def stable_point_id(source_id):
    return str(uuid.uuid5(UUID_NAMESPACE, source_id))


def make_record(filename, source_id, embedding_text, payload):
    payload = {"source_id": source_id, **payload}
    return {
        "file": filename,
        "source_id": source_id,
        "point_id": stable_point_id(source_id),
        "embedding_text": embedding_text,
        "payload": payload,
    }


def build_contacts():
    filename = "contacts.json"
    records = []
    for item in load_json_items(filename):
        source_id = item["id"]
        embedding_text = item["text"]
        payload = {"text": item["text"], **item["metadata"]}
        records.append(make_record(filename, source_id, embedding_text, payload))
    return records


def build_glossary():
    filename = "glossary.py"
    records = []
    for term, definition in load_python_variable(filename, "glossary_data"):
        source_id = f"glossary:{term}"
        embedding_text = f"{term}. {definition}"
        payload = {
            "type": "glossary",
            "term": term,
            "text": definition,
        }
        records.append(make_record(filename, source_id, embedding_text, payload))
    return records


def build_terms_conditions():
    filename = "TermsConditions.py"
    records = []
    for index, item in enumerate(load_python_variable(filename, "documents_data"), start=1):
        source_id = item.get("id") or f"terms-conditions:{index}:{item['title']}"
        title = item["title"]
        content = item["content"]
        embedding_text = f"{title}. {content}"
        payload = {"title": title, "text": content, **item["metadata"]}
        records.append(make_record(filename, source_id, embedding_text, payload))
    return records


def build_title_text_file(filename):
    records = []
    for item in load_json_items(filename):
        source_id = item["id"]
        title = item["title"]
        text = item["text"]
        embedding_text = f"{title}. {text}"
        payload = {"title": title, "text": text, **item["metadata"]}
        records.append(make_record(filename, source_id, embedding_text, payload))
    return records


def build_records():
    records = []
    records.extend(build_contacts())
    records.extend(build_glossary())
    records.extend(build_terms_conditions())
    records.extend(build_title_text_file("details.json"))
    records.extend(build_title_text_file("delivery.json"))
    records.extend(build_title_text_file("general.json"))
    return records


def print_dry_run(records):
    counts = Counter(record["file"] for record in records)

    print("DRY RUN")
    print(f"total points: {len(records)}")
    for filename in [
        "contacts.json",
        "glossary.py",
        "TermsConditions.py",
        "details.json",
        "delivery.json",
        "general.json",
    ]:
        print(f"{filename}: {counts[filename]}")

    print(f"expected total: {EXPECTED_TOTAL}")
    print(f"matches expected: {len(records) == EXPECTED_TOTAL}")
    print()

    sample_files = ["contacts.json", "glossary.py", "TermsConditions.py"]
    sample_records = []
    for sample_file in sample_files:
        sample_records.append(next(record for record in records if record["file"] == sample_file))

    for index, record in enumerate(sample_records, start=1):
        print(f"EXAMPLE {index}")
        print(f"file: {record['file']}")
        print(f"source_id: {record['source_id']}")
        print(f"point_id: {record['point_id']}")
        print(f"embedding_text: {record['embedding_text'][:700]}")
        print("payload:")
        print(json.dumps(record["payload"], ensure_ascii=False, indent=2))
        print()


def upsert_records(records, batch_size):
    from vector_db import client_qrant, get_embedding

    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        points = []
        for record in batch:
            vector = get_embedding(record["embedding_text"])
            points.append(
                PointStruct(
                    id=record["point_id"],
                    vector=vector,
                    payload=record["payload"],
                )
            )
        client_qrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
        print(f"upserted {start + len(batch)}/{len(records)}")


def verify_records():
    from vector_db import client_qrant, get_embedding

    count = client_qrant.count(QDRANT_COLLECTION, exact=True)
    print(f"collection count: {count.count}")
    print()

    points, _ = client_qrant.scroll(
        QDRANT_COLLECTION,
        limit=5,
        with_payload=True,
        with_vectors=False,
    )
    print("SCROLL SAMPLE")
    for point in points:
        print(f"id: {point.id}")
        print(json.dumps(point.payload, ensure_ascii=False, indent=2)[:1500])
        print()

    queries = [
        "какие у вас контакты?",
        "что такое грунтовка?",
        "можно ли вернуть товар?",
        "в воскресенье доставляете?",
        "сколько цветов колеровки?",
    ]
    print("SEMANTIC SEARCH")
    for query in queries:
        vector = get_embedding(query)
        results = client_qrant.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            limit=3,
            with_payload=True,
        )
        print(f"query: {query}")
        for scored_point in results.points:
            payload = scored_point.payload or {}
            label = payload.get("title") or payload.get("term") or payload.get("source_id")
            print(f"  score={scored_point.score:.4f} source_id={payload.get('source_id')} label={label}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Prepare and load data into Qdrant.")
    parser.add_argument("--upsert", action="store_true", help="Create embeddings and upsert into Qdrant.")
    parser.add_argument("--verify", action="store_true", help="Verify Qdrant count, payloads, and search.")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    records = build_records()
    print_dry_run(records)

    if len(records) != EXPECTED_TOTAL:
        raise SystemExit(f"Expected {EXPECTED_TOTAL} points, got {len(records)}")

    if args.upsert:
        upsert_records(records, args.batch_size)

    if args.verify:
        verify_records()


if __name__ == "__main__":
    main()
