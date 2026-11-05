import os
import json
from pathlib import Path
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from dotenv import load_dotenv

RETRIEVE_PROMPT_VERSION = "PR-02-v1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def retrieve_docs(ticket: dict, intent: str, top_k: int = 3) -> list:
    """Retrieves relevant knowledge base documentation chunks."""
    load_dotenv(PROJECT_ROOT / ".env")
    chroma_path = Path(os.getenv("CHROMA_PATH", "storage/chroma"))
    doc_path = Path(os.getenv("DOC_PATH", "data/documentation.json"))
    if not chroma_path.is_absolute():
        chroma_path = PROJECT_ROOT / chroma_path
    if not doc_path.is_absolute():
        doc_path = PROJECT_ROOT / doc_path
    
    if chroma_path.exists():
        try:
            embeddings = HuggingFaceEmbeddings(model_name=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
            store = Chroma(persist_directory=str(chroma_path), embedding_function=embeddings)
            query = f"{intent} {ticket['subject']} {ticket['body']}"
            results = store.similarity_search(query, k=top_k)
            retrieved = []
            for doc in results:
                retrieved.append({
                    "doc_id": doc.metadata.get("doc_id", "DOC-UNKNOWN"),
                    "title": doc.metadata.get("title", ""),
                    "content": doc.page_content
                })
            return retrieved
        except Exception as e:
            pass
            
    # Fallback to direct json search if vector store is uninitialized
    if doc_path.exists():
        with doc_path.open(encoding="utf-8") as f:
            docs = json.load(f)
        matches = []
        for d in docs:
            matches.append({"doc_id": d["doc_id"], "title": d["title"], "content": d["content"]})
        return matches[:top_k]
        
    return []
