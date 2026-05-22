from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, QDRANT_COLLECTION, QDRANT_HOST, QDRANT_PORT

client_qrant = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
client_gemini = genai.Client(api_key=GEMINI_API_KEY)

collection_name = QDRANT_COLLECTION
size = 1536


def create_collection(client, collection_name, size):
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=size, distance=Distance.COSINE)
    )
    print('Collection was created')

def get_embedding(text):
    result = client_gemini.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=1536)
    )
    return result.embeddings[0].values
