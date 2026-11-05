import json
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter

with open("data/documentation.json") as f:
    documents = json.load(f)

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)

texts, metadatas = [], []
for doc in documents:
    for chunk in splitter.split_text(doc["content"]):
        texts.append(chunk)
        metadatas.append({"doc_id": doc["doc_id"], "title": doc["title"]})

embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
store = Chroma.from_texts(
    texts=texts, metadatas=metadatas, embedding=embeddings,
    persist_directory="./storage/chroma",
)
store.persist()
print(f"stored {len(texts)} passages")