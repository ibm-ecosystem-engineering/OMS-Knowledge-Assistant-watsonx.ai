from flask import Flask, request, jsonify, send_from_directory
from langchain.vectorstores import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
from sentence_transformers import SentenceTransformer
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
import json
from typing import Optional, Iterable, List
from flask_cors import CORS
from chromadb.utils import embedding_functions
from ibm_watson_machine_learning.foundation_models import Model
from ibm_cloud_sdk_core import IAMTokenManager
from langchain.schema import Document
from bs4 import BeautifulSoup
import re
import requests

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

class MiniLML6V2EmbeddingFunctionLangchain:
    MODEL = SentenceTransformer('all-MiniLM-L6-v2')

    default_ef = embedding_functions.DefaultEmbeddingFunction()
    def embed_documents(self, texts):
        return MiniLML6V2EmbeddingFunctionLangchain.MODEL.encode(texts).tolist()
    
    def embed_query(self, query):
        return MiniLML6V2EmbeddingFunctionLangchain.MODEL.encode([query]).tolist()
 
class ChromaWithUpsert(Chroma):
    def upsert_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[List[dict]] = None,
        ids: Optional[List[str]] = None,
    ) -> List[str]:
       
        if ids is None:
            import uuid
            ids = [str(uuid.uuid1()) for _ in texts]
        embeddings = None

        if self._embedding_function is not None:
            embeddings = self._embedding_function.embed_documents(texts = list(texts))
            
        self._collection.upsert(
            metadatas=metadatas, embeddings=embeddings, documents=texts, ids=ids
        )
        return ids
    
    def query(self, query_texts:str, n_results:int=5, include: Optional[List[str]]=None):
        self._collection._embedding_function = MiniLML6V2EmbeddingFunctionLangchain.default_ef
        return self._collection.query(
            query_texts=query_texts,
            n_results=n_results,
            include=include
    )

def fetch_and_extract_text(url: str) -> str:
    #headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}
    headers = {
    'User-Agent': 'PostmanRuntime/7.29.0',
    'Accept': '*/*',
    'Cache-Control': 'no-cache',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive'
 }

    response = requests.get(url, headers=headers)
    #print(response)
    soup = BeautifulSoup(response.content, 'html.parser')
    #print(soup)
    #print(soup)
    article = soup.find('article')
    if not article:
        return "No article found on this page."
    
    # Extract title
    title = article.find('h1')
    title_text = title.get_text(strip=True) if title else ""
    
    # Extract main body text
    body = article.find('div', class_='body')
    body_text = body.get_text(separator=' ', strip=True) if body else ""
    # Combine title and body text
    full_text = f"{title_text}\n\n{body_text}"
    return full_text

def crawl_website(base_url: str, paths: List[str]) -> List[str]:

    texts = []
    for path in paths:
        url = f"{base_url}{path}"
        try:
            print(f"Fetching: {url}")
            page_text = fetch_and_extract_text(url)
            texts.append(page_text)
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
    return texts

# Example website and paths
base_url = "https://www.ibm.com/docs/en/order-management-sw/10.0"
paths = [
    "?topic=management-create-item-in-sterling-business-center",
    "?topic=management-define-items-units-measure",
    "?topic=management-modify-delivery-service-associated-item",
    "?topic=items-deleting-item"
]
import time
global start_item
start_time = time.time()
print(start_time)
from datetime import datetime
now = datetime.now()
print(now)
# Crawl the website
data = crawl_website(base_url, paths)

documents = [Document(page_content=text) for text in data]
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

text_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
texts = text_splitter.split_documents(documents)

vector_store = ChromaWithUpsert(
    collection_name=f"store_minilm6v2",
    embedding_function=MiniLML6V2EmbeddingFunctionLangchain()
)

vector_store.upsert_texts(
    texts=[doc.page_content for doc in texts]
)

# IBM Watson configuration
api_key = "###########################################"
watsonx_project_id = "#############################"
model_id = "ibm/granite-13b-chat-v2"
endpoint = "https://us-south.ml.cloud.ibm.com"

try:
    access_token = IAMTokenManager(
        apikey=api_key,
        url="https://iam.cloud.ibm.com/identity/token"
    ).get_token()
except Exception as e:
    print(f'Issue obtaining access token: {e}')

credentials = { 
    "url": endpoint, 
    "token": access_token
}

gen_params = {
    "DECODING_METHOD": "greedy",
    "MAX_NEW_TOKENS": 1000,
    "MIN_NEW_TOKENS": 1,
    #"TEMPERATURE": 0,
    #"TOP_K": 10,
    "REPETITION_PENALTY" : 1.0
}

model = Model(model_id, credentials, gen_params, watsonx_project_id)

prompt_template = """
Answer the following question using the context provided. 
If there is no good answer, say "I don't know".

Context: %s

Question: %s
"""

def augment(template_in, context_in, query_in):
    return template_in % (context_in, query_in)

def generate(model_in, augmented_prompt_in):
    generated_response = model_in.generate(augmented_prompt_in)
    if ("results" in generated_response) \
       and (len(generated_response["results"]) > 0) \
       and ("generated_text" in generated_response["results"][0]):
        return generated_response["results"][0]["generated_text"]
    else:
        print("The model failed to generate an answer")
        print("\nDebug info:\n" + json.dumps(generated_response, indent=3))
        return ""

@app.route('/ask', methods=['POST'])
def ask():
    data = request.json
    question = data.get('question')
    if not question:
        return jsonify({"error": "No question provided"}), 400

    search_k = 3
    docs = vector_store.query(
        query_texts=[question],
        n_results=search_k,
        include=["documents", "metadatas", "distances"]
    )

    context = " ".join(docs["documents"][0])

    augmented_prompt = augment(prompt_template, context, question)
    output = generate(model, augmented_prompt)
    print(output)
    end = datetime.now()
    elapsed_datetime = end - now
    print(elapsed_datetime)

    end_time = time.time()
    print(end_time)
    elapsed_time = end_time - start_time
    print(elapsed_time)
    return jsonify({"answer": output})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)

