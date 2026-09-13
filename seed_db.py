import os
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions

# Configuration
DOCS_DIR = Path("./documentation")
DB_DIR = Path("./chroma_db")
COLLECTION_NAME = "stm32_docs"

def seed_database():
    """
    Extracts text from documentation files and seeds the ChromaDB local store.
    Uses a high-quality Sentence-Transformer model for embeddings.
    """
    print(f"Initializing ChromaDB at {DB_DIR}...")
    client = chromadb.PersistentClient(path=str(DB_DIR))
    
    # Use a high-quality embedding model (all-MiniLM-L6-v2 is fast and effective)
    # ChromaDB's DefaultEmbeddingFunction uses this by default, but we'll be explicit.
    emb_fn = embedding_functions.DefaultEmbeddingFunction()
    
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME, 
        embedding_function=emb_fn
    )

    # Clear existing data to avoid duplicates during re-seeding
    if collection.count() > 0:
        print("Clearing existing collection...")
        # ChromaDB delete requires a filter. To clear all, we can delete by IDs.
        all_ids = collection.get()["ids"]
        if all_ids:
            collection.delete(ids=all_ids)

    # Process documents
    # Note: In a real scenario, you'd use a PDF parser like PyPDF2 or pdfplumber.
    # For this implementation, we assume text extraction is handled or 
    # we process .txt files.
    
    documents_processed = 0
    for doc_file in DOCS_DIR.glob("**/*.txt"):
        print(f"Processing {doc_file.name}...")
        with open(doc_file, "r", encoding="utf-8") as f:
            text = f.read()
            # Simple chunking by paragraph
            chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
            
            ids = [f"{doc_file.name}_{i}" for i in range(len(chunks))]
            collection.add(
                documents=chunks,
                ids=ids,
                metadatas=[{"source": str(doc_file)} for _ in chunks]
            )
            documents_processed += len(chunks)

    # If no .txt files, we seed with the core hardware facts known to the project
    if documents_processed == 0:
        print("No .txt files found in documentation folder. Seeding with core hardware facts...")
        core_facts = [
            "STM32F4-Discovery board (board id disco_f407vg, MCU STM32F407VGT6) user LEDs: LD4 green=PD12, LD3 orange=PD13, LD5 red=PD14, LD6 blue=PD15. All four are active-high and on GPIO port D; enable clock via RCC_AHB1ENR GPIODEN before use.",
            "Do not use PC13 as an LED pin on the disco_f407vg board - PC13 is the blue LED pin on other, unrelated STM32F407 boards, not on the STM32F4-Discovery board.",
            "PA5 on the disco_f407vg board is SPI1_SCK and is not wired to any LED.",
            "Clock: HSE 8MHz -> PLL (M=8 N=336 P=2 Q=7) -> 168MHz system clock.",
            "GPIO init: enable the port clock first (e.g. RCC_AHB1ENR), then set MODE_OUTPUT_PP, PULL_NONE, SPEED_50MHz for a simple LED output.",
            "UART2: TX=PA2 RX=PA3, enable clock via RCC_APB1ENR USART2EN.",
            "SPI1 default pins: SCK=PA5 MISO=PA6 MOSI=PA7.",
        ]
        ids = [f"core-fact-{i}" for i in range(len(core_facts))]
        collection.add(documents=core_facts, ids=ids)
        print(f"Seeded {len(core_facts)} core hardware facts.")

    print(f"Database seeding complete. Total documents in collection: {collection.count()}")

if __name__ == "__main__":
    seed_database()
