import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_index(chunk_size=800, chunk_overlap=120):
    load_dotenv(PROJECT_ROOT / ".env")
    with (PROJECT_ROOT / "data" / "documentation.json").open(encoding="utf-8") as source:
        documents = json.load(source)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    texts = []
    metadatas = []
    for document in documents:
        for chunk in splitter.split_text(document["content"]):
            texts.append(chunk)
            metadatas.append(
                {"doc_id": document["doc_id"], "title": document["title"]}
            )

    persist_directory = PROJECT_ROOT / os.getenv("CHROMA_PATH", "storage/chroma")
    persist_directory.mkdir(parents=True, exist_ok=True)
    embeddings = HuggingFaceEmbeddings(
        model_name=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    )
    store = Chroma.from_texts(
        texts=texts,
        metadatas=metadatas,
        embedding=embeddings,
        persist_directory=str(persist_directory),
    )
    store.persist()
    return len(texts)


def main():
    passage_count = build_index()
    print(f"stored {passage_count} passages")


if __name__ == "__main__":
    main()
