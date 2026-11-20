import os
import json
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from dotenv import load_dotenv
from src.config import PROJECT_ROOT, env_value

RETRIEVE_PROMPT_VERSION = "PR-02-v1.0"
PROJECT_ROOT = PROJECT_ROOT


@lru_cache(maxsize=2)
def _vector_store(chroma_path: str, model_name: str):
    embeddings = HuggingFaceEmbeddings(model_name=model_name)
    return Chroma(persist_directory=chroma_path, embedding_function=embeddings)


def warm_up_retriever():
    """Load the embedding model once; returns seconds taken, or None if no index exists."""
    load_dotenv(PROJECT_ROOT / ".env")
    chroma_path = Path(env_value("CHROMA_PATH"))
    if not chroma_path.is_absolute():
        chroma_path = PROJECT_ROOT / chroma_path
    if not chroma_path.exists():
        return None
    started = perf_counter()
    try:
        _vector_store(str(chroma_path), env_value("EMBEDDING_MODEL"))
    except Exception:
        return None
    return perf_counter() - started


def retrieve_docs(ticket: dict, intent: str, top_k: int = 3) -> list:
    """Retrieves relevant knowledge base documentation chunks."""
    load_dotenv(PROJECT_ROOT / ".env")
    chroma_path = Path(env_value("CHROMA_PATH"))
    doc_path = Path(os.getenv("DOC_PATH", "data/documentation.json"))
    if not chroma_path.is_absolute():
        chroma_path = PROJECT_ROOT / chroma_path
    if not doc_path.is_absolute():
        doc_path = PROJECT_ROOT / doc_path
    
    source_docs = []
    if doc_path.exists():
        with doc_path.open(encoding="utf-8") as source:
            source_docs = json.load(source)
    review_age_by_id = {
        document["doc_id"]: document.get("last_reviewed_days_ago")
        for document in source_docs
    }

    if chroma_path.exists():
        try:
            store = _vector_store(
                str(chroma_path), env_value("EMBEDDING_MODEL")
            )
            query = f"{intent} {ticket['subject']} {ticket['body']}"
            results = store.similarity_search_with_relevance_scores(query, k=top_k)
            retrieved = []
            for doc, score in results:
                retrieved.append({
                    "doc_id": doc.metadata.get("doc_id", "DOC-UNKNOWN"),
                    "title": doc.metadata.get("title", ""),
                    "content": doc.page_content,
                    "score": float(score),
                    "review_age_days": review_age_by_id.get(doc.metadata.get("doc_id")),
                    "retrieval_mode": "vector",
                })
            return retrieved
        except Exception:
            pass
            
    # Fallback to direct json search if vector store is uninitialized
    if source_docs:
        matches = []
        for document in source_docs:
            matches.append({
                "doc_id": document["doc_id"],
                "title": document["title"],
                "content": document["content"],
                "score": None,
                "review_age_days": document.get("last_reviewed_days_ago", 0),
                "retrieval_mode": "json_fallback",
            })
        return matches[:top_k]
        
    return []
